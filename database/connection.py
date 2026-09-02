"""PostgreSQL connection and schema bootstrap.

DAO/service code calls ``with connect(database_url=...) as conn:``. Behind
that interface we now keep a ``psycopg_pool.ConnectionPool`` per
database URL, so repeated DAO calls on the hot path don't pay the
TCP+auth+timezone-set roundtrip every time. The first ``connect()`` for
each URL warms the pool lazily; subsequent calls hand back pooled
connections. Pool size is governed by ``DB_POOL_MIN_SIZE`` /
``DB_POOL_MAX_SIZE`` env vars (defaults 1 / 20).

The lifespan startup is responsible for calling ``close_all_pools()`` on
shutdown. Tests don't have to: pools are idle-safe and the test fixture
truncates between cases without holding connections.
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlparse

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

SCHEMA_PATH = Path(__file__).resolve().parent / "schema_postgresql.sql"


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _database_name(database_url: str) -> str:
    parsed = urlparse(database_url)
    return parsed.path.lstrip("/").split("/", 1)[0]


def _assert_regression_database(database_url: str) -> None:
    if not _env_flag("AUTOGEN_REGRESSION_MODE"):
        return

    expected_name = os.environ.get("AUTOGEN_REGRESSION_DATABASE", "autogen_test").strip()
    actual_name = _database_name(database_url)
    if actual_name != expected_name:
        raise RuntimeError(
            "AUTOGEN_REGRESSION_MODE is enabled, but DATABASE_URL points to "
            f"{actual_name!r}. Regression tests must write only to {expected_name!r}."
        )


def get_database_url(explicit: Optional[str] = None) -> str:
    if explicit and explicit.strip():
        url = explicit.strip()
        _assert_regression_database(url)
        return url
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Example: "
            "postgresql://user:pass@localhost:5432/autogen"
        )
    _assert_regression_database(url)
    return url


_POOLS: Dict[str, ConnectionPool] = {}
_POOLS_LOCK = threading.Lock()


def _pool_options() -> Dict[str, int]:
    return {
        "min_size": max(0, int(os.getenv("DB_POOL_MIN_SIZE", "1"))),
        "max_size": max(1, int(os.getenv("DB_POOL_MAX_SIZE", "20"))),
        "timeout": float(os.getenv("DB_POOL_CHECKOUT_TIMEOUT_SECONDS", "30")),
    }


def _get_or_create_pool(url: str) -> ConnectionPool:
    pool = _POOLS.get(url)
    if pool is not None and not pool.closed:
        return pool
    with _POOLS_LOCK:
        pool = _POOLS.get(url)
        if pool is not None and not pool.closed:
            return pool
        opts = _pool_options()
        pool = ConnectionPool(
            url,
            kwargs={"options": "-c timezone=UTC", "row_factory": dict_row},
            min_size=opts["min_size"],
            max_size=opts["max_size"],
            timeout=opts["timeout"],
            open=True,
        )
        _POOLS[url] = pool
    return pool


def close_all_pools() -> None:
    """Close every cached pool (lifespan shutdown / test teardown)."""
    with _POOLS_LOCK:
        urls = list(_POOLS.keys())
        for url in urls:
            pool = _POOLS.pop(url, None)
            if pool is not None:
                try:
                    pool.close()
                except Exception:
                    pass


@contextmanager
def connect(database_url: Optional[str] = None, **kwargs):
    """Yield a pooled psycopg connection.

    The connection is checked out from the per-URL ``ConnectionPool`` for
    the duration of the ``with`` block and returned (not closed) on exit.
    The first call for a given URL warms the pool lazily. Callers that
    need a non-pooled connection (e.g. CLI utilities that exit
    immediately) can pass ``_pool=False``.
    """
    # Allow opt-out for short-lived CLI scripts that don't want to keep
    # the pool around.
    use_pool = kwargs.pop("_pool", True)
    url = get_database_url(database_url)

    if not use_pool:
        options = kwargs.pop("options", None)
        if options:
            if "timezone=UTC" not in options:
                options = f"{options} -c timezone=UTC"
        else:
            options = "-c timezone=UTC"
        conn = psycopg.connect(url, row_factory=dict_row, options=options, **kwargs)
        try:
            yield conn
        finally:
            conn.close()
        return

    pool = _get_or_create_pool(url)
    with pool.connection() as conn:
        yield conn


def init_schema(conn: psycopg.Connection) -> None:
    """Apply the canonical schema and any in-place migrations.

    The project intentionally does NOT use Alembic / yoyo (business
    decision 2026-05-09). Schema evolution lives
    entirely in this function plus `schema_postgresql.sql`:

      * additions go to schema_postgresql.sql so a fresh DB is correct
      * existing-DB migrations are appended below as idempotent
        `ALTER TABLE IF NOT EXISTS / ADD COLUMN IF NOT EXISTS` blocks
      * DAO read paths use `try/except UndefinedColumn` (psycopg) as a
        belt-and-braces fallback for the brief window between deploy
        and init_schema rerun

    Keep migrations idempotent and order-independent. If you need
    transactional rollbacks, dependency tracking, or per-row data
    backfills, that is the trigger to revisit F5 and introduce Alembic.
    """
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        # Serialise concurrent init_schema calls (rolling deploy /
        # multi-worker startup) so two processes don't both pass IF NOT EXISTS
        # checks and then collide on commit. pg_advisory_xact_lock releases at
        # transaction end automatically — must be the FIRST statement inside the
        # single transaction (any DDL before it leaves the window open).
        cur.execute("SELECT pg_advisory_xact_lock(817464923)")
        cur.execute(sql)
        cur.execute(
            """
            ALTER TABLE projects
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            """
        )
        cur.execute(
            """
            UPDATE projects
            SET updated_at = created_at
            WHERE updated_at IS NULL
            """
        )

        # System-level audit events (e.g. admin user-mgmt actions) need to
        # log without an associated project. Make project_id nullable so
        # we can persist those rows. Idempotent.
        cur.execute(
            "ALTER TABLE execution_logs ALTER COLUMN project_id DROP NOT NULL"
        )

        # Composite indexes for timeline / artifact-type access
        # patterns. CREATE INDEX IF NOT EXISTS is idempotent and cheap on
        # warm DBs; on cold DBs it's a one-time backfill.
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_messages_project_timestamp
                ON messages(project_id, timestamp DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_execution_logs_project_created
                ON execution_logs(project_id, created_at DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_artifacts_project_type
                ON artifacts(project_id, artifact_type)
            """
        )
        # Deduplicate before creating the unique index so this migration is safe
        # on existing DBs that may have accumulated duplicate rows.  Keep the
        # row with the smallest id (earliest insert) for each group.
        cur.execute(
            """
            DELETE FROM artifacts a
            USING (
                SELECT MIN(id) AS keep_id, project_id, artifact_type, file_path
                FROM artifacts
                GROUP BY project_id, artifact_type, file_path
                HAVING COUNT(*) > 1
            ) dups
            WHERE a.project_id  = dups.project_id
              AND a.artifact_type = dups.artifact_type
              AND a.file_path    = dups.file_path
              AND a.id           != dups.keep_id
            """
        )
        cur.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_artifacts_project_type_path
                ON artifacts(project_id, artifact_type, file_path)
            """
        )

        # Schema optimisation pass — indexes identified by query-pattern audit.
        # All statements are idempotent (IF NOT EXISTS / IF EXISTS).

        # Drop redundant duplicate of the implicit index created by UNIQUE constraint.
        cur.execute("DROP INDEX IF EXISTS idx_users_email")

        # projects: status filter (reconcile, cleanup) and sort-key (list endpoint).
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status)"
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_projects_sort_key
                ON projects(COALESCE(updated_at, created_at) DESC)
            """
        )

        # stages: composite covers the hot get_or_start_stage / pause_stage pattern.
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_stages_project_name_time
                ON stages(project_id, stage_name, start_time DESC NULLS LAST)
            """
        )

        # messages: stage-filtered queries (get_project_messages with stage filter).
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_messages_project_stage
                ON messages(project_id, stage_name, timestamp DESC)
            """
        )

        # artifacts: partial functional index for cross-project S2 paper dedup.
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_artifacts_provenance_s2id
                ON artifacts((provenance->>'s2_paper_id'))
                WHERE provenance->>'s2_paper_id' IS NOT NULL
            """
        )

        # Per-project LLM model selection.
        # NULL means "use env-var default" — fully backwards-compatible.
        cur.execute(
            """
            ALTER TABLE projects
            ADD COLUMN IF NOT EXISTS analysis_model TEXT DEFAULT NULL
            """
        )
        cur.execute(
            """
            ALTER TABLE projects
            ADD COLUMN IF NOT EXISTS download_model TEXT DEFAULT NULL
            """
        )

        # PubMed migration: date range + search_backend
        # columns persisted on projects so resume / start_pipeline can recover
        # them, and dedup branches on which search backend the project was
        # created under.
        cur.execute(
            """
            ALTER TABLE projects
            ADD COLUMN IF NOT EXISTS date_start TEXT DEFAULT NULL
            """
        )
        cur.execute(
            """
            ALTER TABLE projects
            ADD COLUMN IF NOT EXISTS date_end TEXT DEFAULT NULL
            """
        )
        cur.execute(
            """
            ALTER TABLE projects
            ADD COLUMN IF NOT EXISTS search_backend TEXT DEFAULT 'pubmed'
            """
        )
        #  originally included a backfill UPDATE that marked
        # pre-deploy historical projects as search_backend='s2'. The current
        # deployment has no historical S2 projects to migrate, so the
        # backfill UPDATE + its deploy-timestamp placeholder were
        # removed as dead code. New projects use the DEFAULT 'pubmed' value
        # from the schema ALTER above; that's the only behavior we need.
        # Functional index parallel to idx_artifacts_provenance_s2id, for the
        # PubMed PMID dedup queries (has_paper_artifact_by_pmid /
        # find_paper_in_other_user_projects_by_pmid).
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_artifacts_provenance_pmid
                ON artifacts((provenance->>'pmid'))
                WHERE provenance->>'pmid' IS NOT NULL
                  AND provenance->>'pmid' != ''
            """
        )

        # global papers table + analysis_cache table.
        # PMID-only PK. analysis_cache has NO user_email / project_id —
        # privacy boundary .
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS papers (
                pmid TEXT PRIMARY KEY,
                doi TEXT,
                pmcid TEXT,
                title TEXT,
                authors TEXT,
                publication_date TEXT,
                journal TEXT,
                first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_papers_doi
                ON papers(doi) WHERE doi IS NOT NULL
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS analysis_cache (
                id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                pmid TEXT NOT NULL,
                model_name TEXT NOT NULL,
                prompt_hash TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                analysis_markdown TEXT NOT NULL,
                plan_json JSONB,
                data_classification_flag TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (pmid) REFERENCES papers(pmid) ON DELETE CASCADE,
                UNIQUE (pmid, model_name, prompt_hash, content_hash)
            )
            """
        )

        # pre-screen scores table (recall audit). Keyed
        # (project_id, paper_id); written at score time for every scored
        # candidate incl. filtered + not-downloaded ones. DAO upserts via
        # ON CONFLICT (project_id, paper_id).
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS prescreen_scores (
                id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                project_id TEXT,
                paper_id TEXT NOT NULL,
                prescreen_score DOUBLE PRECISION,
                prescreen_model TEXT,
                threshold DOUBLE PRECISION,
                decision TEXT,
                scored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (project_id, paper_id)
            )
            """
        )

        # cross-project pre-screen cache (avoid re-scoring the
        # same paper across same-keyword projects). Global asset like
        # analysis_cache — carries NO project_id / user_email. Keyed
        # (paper_id, model_name, prompt_hash, content_hash); no FK to papers
        # because prescreen runs BEFORE papers rows are upserted.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS prescreen_cache (
                id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                paper_id TEXT NOT NULL,
                model_name TEXT NOT NULL,
                prompt_hash TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                prescreen_score DOUBLE PRECISION NOT NULL,
                prescreen_reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (paper_id, model_name, prompt_hash, content_hash)
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_prescreen_cache_lookup
                ON prescreen_cache (model_name, prompt_hash, paper_id)
            """
        )

        # per-project force_reanalyze flag for B4 escape hatch.
        cur.execute(
            """
            ALTER TABLE projects
            ADD COLUMN IF NOT EXISTS force_reanalyze BOOLEAN DEFAULT FALSE
            """
        )

        # MeSH expansion settings.
        cur.execute(
            """
            ALTER TABLE projects
            ADD COLUMN IF NOT EXISTS mesh_expansion BOOLEAN DEFAULT TRUE
            """
        )
        cur.execute(
            """
            ALTER TABLE projects
            ADD COLUMN IF NOT EXISTS mesh_expanded_query TEXT DEFAULT NULL
            """
        )
        cur.execute(
            """
            ALTER TABLE projects
            ADD COLUMN IF NOT EXISTS mesh_expansion_status TEXT DEFAULT 'disabled'
            """
        )

        # Review Queue status column + partial index.
        cur.execute(
            """
            ALTER TABLE artifacts
            ADD COLUMN IF NOT EXISTS human_review_status TEXT DEFAULT 'pending'
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_artifacts_review_status
                ON artifacts(human_review_status, project_id)
                WHERE data_classification_flag IN ('contact_author', 'manual_required')
            """
        )

        # qualify-stage verdict column + partial index covering
        # BOTH dataset artifact types the qualify stage processes.
        # Values: pending / processing / strict / loose / fail / error.
        cur.execute(
            """
            ALTER TABLE artifacts
            ADD COLUMN IF NOT EXISTS qualification_status TEXT DEFAULT 'pending'
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_artifacts_qualification_status
                ON artifacts(qualification_status, project_id)
                WHERE artifact_type IN ('dataset', 'embedded_dataset')
            """
        )

        # Decisive reason for the qualify verdict (shown inline with the verdict).
        cur.execute(
            "ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS qualification_reason TEXT"
        )

        # Per-paper, per-stage LLM cost telemetry (analyze / download / qualify):
        # single source of truth for tokens / cost / duration. Per-paper total
        # cost is SUM(cost_usd) GROUP BY paper_name.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS stage_costs (
                id SERIAL PRIMARY KEY,
                project_id TEXT NOT NULL,
                paper_name TEXT NOT NULL,
                stage TEXT NOT NULL,
                model TEXT,
                input_tokens BIGINT DEFAULT 0,
                output_tokens BIGINT DEFAULT 0,
                cost_usd DOUBLE PRECISION DEFAULT 0,
                duration_ms BIGINT DEFAULT 0,
                created_at TIMESTAMPTZ DEFAULT now(),
                UNIQUE (project_id, paper_name, stage),
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_stage_costs_project ON stage_costs(project_id)"
        )

        # Widen artifacts.file_size from INTEGER → BIGINT. The old INTEGER
        # type tops out at 2,147,483,647 bytes (~2 GB); a deployment can hit
        # `ERROR: integer out of range` on any multi-GB artifact. The failure
        # cascades: the rollback orphans the already-uploaded blob, breaks the
        # download stage's transaction, and can fill the WAL volume. BIGINT
        # trivially covers any file size we'll see (up to 9.2 EB). ALTER is
        # idempotent: a column that's already BIGINT silently succeeds.
        cur.execute(
            """
            ALTER TABLE artifacts
            ALTER COLUMN file_size TYPE BIGINT
            """
        )

    conn.commit()


def truncate_all_tables(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        # Include analysis_cache + papers so integration
        # tests start each case with a clean cache state. analysis_cache must
        # come before papers (FK dependency); RESTART IDENTITY CASCADE is
        # tolerant of FK ordering but listing explicit order is clearer.
        cur.execute(
            """
            TRUNCATE TABLE projects, users, linked_accounts, analysis_cache, papers, prescreen_scores, prescreen_cache
            RESTART IDENTITY CASCADE
            """
        )
    conn.commit()

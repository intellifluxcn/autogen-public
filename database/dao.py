"""Data Access Object for project persistence (PostgreSQL)."""

import logging
import os
import uuid
import json
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

from psycopg.errors import ForeignKeyViolation, UndefinedColumn, UndefinedTable
from psycopg.types.json import Json

from database.connection import connect, get_database_url, init_schema
from utils.demo_user import DEMO_ADMIN_EMAIL
from utils.pipeline_log import pipeline_log

logger = logging.getLogger(__name__)

_PROJECT_LIST_FILTER_STATUSES = frozenset(
    {"pending", "running", "paused", "completed", "failed"}
)


class ProjectDAO:

    def __init__(self, database_url: Optional[str] = None):
        self.database_url = database_url
        self._init_database()

    def _init_database(self) -> None:
        with connect(self.database_url) as conn:
            init_schema(conn)

    def _get_url(self) -> str:
        return get_database_url(self.database_url)

    def create_project(
        self,
        name: str,
        research_query: str,
        user_email: str = DEMO_ADMIN_EMAIL,
        max_papers: int = 10,
        parallel_pipeline: bool = False,
        analysis_model: Optional[str] = None,
        download_model: Optional[str] = None,
        date_start: Optional[str] = None,
        date_end: Optional[str] = None,
        search_backend: str = "pubmed",
        force_reanalyze: bool = False,
        mesh_expansion: bool = True,
        status: str = "running",
    ) -> str:
        project_id = str(uuid.uuid4())
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO projects (
                        id, name, research_query, user_email, max_papers,
                        parallel_pipeline, status, analysis_model, download_model,
                        date_start, date_end, search_backend, force_reanalyze,
                        mesh_expansion
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (project_id, name, research_query, user_email, max_papers,
                     parallel_pipeline, status, analysis_model, download_model,
                     date_start, date_end, search_backend, force_reanalyze,
                     mesh_expansion),
                )
            conn.commit()
        return project_id

    # ------------------------------------------------------------------
    # Review Queue + ownership-aware lookup
    # ------------------------------------------------------------------

    def get_artifact_for_user(
        self, artifact_id: int, user_email: str,
    ) -> Optional[Dict]:
        """Round 1 review fix: ownership-aware artifact fetch.

        Joins projects.user_email to enforce per-user isolation. Returns None
        when not owned (caller must 404 to avoid leaking existence).
        """
        if not artifact_id or not user_email:
            return None
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        SELECT a.*
                        FROM artifacts a
                        JOIN projects p ON a.project_id = p.id
                        WHERE a.id = %s AND p.user_email = %s
                        """,
                        (artifact_id, user_email),
                    )
                    row = cur.fetchone()
                    return dict(row) if row else None
                except Exception:
                    return None

    def get_review_queue(
        self,
        user_email: str,
        *,
        status_filter: Optional[List[str]] = None,
        page: int = 1,
        page_size: int = 20,
        project_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """ / Tier 2/3 artifacts needing user review.

        Returns ``{items, total, page, page_size}``.

        Privacy boundary: the user_email JOIN strictly isolates per-user.
        Correctness: the WHERE clause has an EXPLICIT
        ``data_classification_flag IN (...)`` predicate — never rely solely
        on the partial index's predicate to drive correctness.

        An optional ``project_id`` filter narrows results to a single
        project. Ownership is enforced by the existing ``p.user_email = %s``
        JOIN predicate — an unowned project_id silently returns 0 rows
        (equivalent to "project does not exist" from the caller's view).
        """
        if not user_email:
            return {"items": [], "total": 0, "page": page, "page_size": page_size}
        statuses = status_filter or ["pending"]
        offset = max(0, (page - 1) * page_size)
        project_clause = " AND a.project_id = %s" if project_id is not None else ""
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                try:
                    count_params: Tuple[Any, ...] = (user_email, statuses)
                    if project_id is not None:
                        count_params = count_params + (project_id,)
                    cur.execute(
                        f"""
                        SELECT COUNT(*) AS total FROM artifacts a
                        JOIN projects p ON a.project_id = p.id
                        WHERE p.user_email = %s
                          AND a.human_review_status = ANY(%s)
                          AND a.data_classification_flag IN ('contact_author', 'manual_required')
                          {project_clause}
                        """,
                        count_params,
                    )
                    total_row = cur.fetchone()
                    total = (total_row["total"] if total_row else 0) or 0

                    select_params: Tuple[Any, ...] = (user_email, statuses)
                    if project_id is not None:
                        select_params = select_params + (project_id,)
                    select_params = select_params + (page_size, offset)
                    # has_actionable_download: did the download stage already
                    # produce a non-failed artifact for this paper? We surface
                    # it so the UI can hide "resume download" when re-running won't
                    # improve the outcome (a manual_required paper that already
                    # got a partial embedded_extraction will only get the same
                    # partial result again — the LLM concluded auto-download
                    # isn't viable, and that conclusion is sticky).
                    #
                    # The paper stem is derived from the analysis artifact
                    # file_name (e.g. "Marc2015_2.md" → "Marc2015_2"), then
                    # matched against datasets/<stem>/* paths in the same
                    # project. Includes acquisition_evidence so embedded-only
                    # acquisitions (the typical manual_required outcome) count
                    # as actionable too.
                    cur.execute(
                        f"""
                        SELECT a.id, a.project_id, p.name AS project_name,
                               a.artifact_type, a.file_name, a.file_path,
                               a.data_classification_flag,
                               a.human_review_status, a.created_at,
                               a.provenance,
                               EXISTS (
                                 SELECT 1 FROM artifacts d
                                 WHERE d.project_id = a.project_id
                                   AND d.artifact_type IN (
                                     'dataset', 'embedded_dataset',
                                     'acquisition_evidence'
                                   )
                                   AND d.acquisition_status IN (
                                     'completed', 'partial', 'awaiting_external'
                                   )
                                   AND position(
                                     '/datasets/' ||
                                     regexp_replace(a.file_name, '\\.md$', '') ||
                                     '/' IN d.file_path
                                   ) > 0
                               ) AS has_actionable_download,
                               (a.provenance ? 'cache_key'
                                AND (a.provenance->'cache_key' ? 'pmid')
                                AND (a.provenance->'cache_key' ? 'model_name')
                                AND (a.provenance->'cache_key' ? 'prompt_hash')
                                AND (a.provenance->'cache_key' ? 'content_hash')
                               ) AS has_cached_plan
                        FROM artifacts a
                        JOIN projects p ON a.project_id = p.id
                        WHERE p.user_email = %s
                          AND a.human_review_status = ANY(%s)
                          AND a.data_classification_flag IN ('contact_author', 'manual_required')
                          {project_clause}
                        ORDER BY a.created_at DESC
                        LIMIT %s OFFSET %s
                        """,
                        select_params,
                    )
                    rows = cur.fetchall()
                    items = [dict(r) for r in rows]
                except Exception:
                    return {"items": [], "total": 0, "page": page, "page_size": page_size}
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    def update_artifact_review_status(
        self,
        artifact_id: int,
        user_email: str,
        new_status: str,
        only_from_statuses: Optional[Tuple[str, ...]] = None,
    ) -> bool:
        """Update human_review_status with ownership +
        Tier 2/3 + optional compare-and-set guards.

        Valid system transitions:
          pending → processing | skipped
          processing → handled | skipped | pending (system retry only)
          {handled, skipped} → terminal

        Returns True if exactly one row was updated, False otherwise.
        Callers map False to 409 Conflict (status changed) or 404 (not found
        / not Tier 2/3).
        """
        if not artifact_id or not user_email or not new_status:
            return False
        from_statuses = list(only_from_statuses) if only_from_statuses else None
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                try:
                    if from_statuses:
                        cur.execute(
                            """
                            UPDATE artifacts a
                            SET human_review_status = %s
                            FROM projects p
                            WHERE a.project_id = p.id
                              AND a.id = %s
                              AND p.user_email = %s
                              AND a.data_classification_flag IN ('contact_author', 'manual_required')
                              AND a.human_review_status = ANY(%s)
                            """,
                            (new_status, artifact_id, user_email, from_statuses),
                        )
                    else:
                        cur.execute(
                            """
                            UPDATE artifacts a
                            SET human_review_status = %s
                            FROM projects p
                            WHERE a.project_id = p.id
                              AND a.id = %s
                              AND p.user_email = %s
                              AND a.data_classification_flag IN ('contact_author', 'manual_required')
                            """,
                            (new_status, artifact_id, user_email),
                        )
                    conn.commit()
                    return cur.rowcount == 1
                except UndefinedColumn:
                    conn.rollback()
                    return False
                except Exception:
                    conn.rollback()
                    return False

    def update_artifact_provenance_for_path(
        self,
        project_id: str,
        file_path: str,
        provenance_patch: Dict[str, Any],
    ) -> bool:
        """ backport: merge ``provenance_patch`` into the
        existing JSONB ``provenance`` of the artifact identified by
        ``(project_id, file_path)``. Idempotent — existing keys are
        overwritten by patch values; absent keys retained.
        """
        if not project_id or not file_path or not provenance_patch:
            return False
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        UPDATE artifacts
                        SET provenance = COALESCE(provenance, '{}'::jsonb) || %s::jsonb
                        WHERE project_id = %s AND file_path = %s
                        """,
                        (Json(provenance_patch), project_id, file_path),
                    )
                    conn.commit()
                    return cur.rowcount > 0
                except UndefinedColumn:
                    conn.rollback()
                    return False
                except Exception:
                    conn.rollback()
                    return False

    def set_mesh_expansion_result(
        self, project_id: str, expanded_query: Optional[str], status: str,
    ) -> None:
        """Persist the MeSH-expanded query + status so
        the UI can show before/after; observability for ops debugging.

        Best-effort — caller catches and ignores exceptions. UndefinedColumn
        is the rolling-deploy-window case (handled silently).
        """
        if not project_id:
            return
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        UPDATE projects
                        SET mesh_expanded_query = %s,
                            mesh_expansion_status = %s
                        WHERE id = %s
                        """,
                        (expanded_query, status, project_id),
                    )
                    conn.commit()
                except UndefinedColumn:
                    conn.rollback()  # init_schema not yet run on this worker
                except Exception:
                    conn.rollback()

    def get_project(self, project_id: str) -> Optional[Dict]:
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM projects WHERE id = %s", (project_id,))
                row = cur.fetchone()
        return dict(row) if row else None

    def get_project_by_name(self, name: str) -> Optional[Dict]:
        """Most recently created project with this exact name, or None."""
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM projects WHERE name = %s "
                    "ORDER BY created_at DESC LIMIT 1",
                    (name,),
                )
                row = cur.fetchone()
        return dict(row) if row else None

    def get_all_projects(self, user_email: Optional[str] = None) -> List[Dict]:
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                if user_email:
                    cur.execute(
                        "SELECT * FROM projects WHERE user_email = %s "
                        "ORDER BY COALESCE(updated_at, created_at) DESC, created_at DESC",
                        (user_email,),
                    )
                else:
                    cur.execute(
                        "SELECT * FROM projects "
                        "ORDER BY COALESCE(updated_at, created_at) DESC, created_at DESC"
                    )
                rows = cur.fetchall()
        return [dict(row) for row in rows]

    def _visible_projects_where(
        self,
        user_email: str,
        search: Optional[str],
        status: Optional[str] = None,
    ) -> tuple[str, List]:
        conditions = ["status != 'cancelled'", "user_email = %s"]
        params: List = [user_email]
        if search and search.strip():
            conditions.append("(name ILIKE %s OR research_query ILIKE %s)")
            term = f"%{search.strip()}%"
            params.extend([term, term])
        if status and status in _PROJECT_LIST_FILTER_STATUSES:
            conditions.append("status = %s")
            params.append(status)
        return " AND ".join(conditions), params

    def count_projects_visible(
        self,
        user_email: str,
        search: Optional[str] = None,
        status: Optional[str] = None,
    ) -> int:
        where_sql, params = self._visible_projects_where(user_email, search, status)
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT COUNT(*) AS n FROM projects WHERE {where_sql}",
                    tuple(params),
                )
                row = cur.fetchone()
        return int(row["n"]) if row and row["n"] is not None else 0

    def get_projects_page(
        self,
        user_email: str,
        search: Optional[str],
        page: int,
        page_size: int,
        status: Optional[str] = None,
    ) -> List[Dict]:
        where_sql, params = self._visible_projects_where(user_email, search, status)
        offset = max(0, (max(1, page) - 1) * page_size)
        lim_params = list(params) + [page_size, offset]
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT * FROM projects
                    WHERE {where_sql}
                    ORDER BY COALESCE(updated_at, created_at) DESC, created_at DESC
                    LIMIT %s OFFSET %s
                    """,
                    tuple(lim_params),
                )
                rows = cur.fetchall()
        return [dict(row) for row in rows]

    def get_projects_page_with_summary(
        self,
        user_email: str,
        search: Optional[str],
        page: int,
        page_size: int,
        status: Optional[str] = None,
    ) -> Tuple[List[Dict], Dict[str, List[Dict]], Dict[str, Dict[str, int]]]:
        """Page of projects + per-project stages + per-project artifact counts
        in 3 queries instead of 1+2N. Returns:

          (project_rows, stages_by_project_id, artifact_counts_by_project_id)

        - stages_by_project_id maps project_id → list of stage rows.
        - artifact_counts_by_project_id maps project_id → {stage_name: count}.
          The count semantics match the previous per-call build path:
          download-stage artifacts only count when artifact_type is in
          ('dataset', 'embedded_dataset').
        """
        project_rows = self.get_projects_page(user_email, search, page, page_size, status)
        if not project_rows:
            return ([], {}, {})

        project_ids = [row["id"] for row in project_rows]

        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM stages
                    WHERE project_id = ANY(%s)
                    ORDER BY project_id,
                             CASE stage_name
                                 WHEN 'find' THEN 1
                                 WHEN 'analyze' THEN 2
                                 WHEN 'download' THEN 3
                                 ELSE 4
                             END
                    """,
                    (project_ids,),
                )
                stage_rows = cur.fetchall()

                cur.execute(
                    """
                    SELECT
                        project_id,
                        stage_name,
                        COUNT(*) FILTER (
                            WHERE stage_name <> 'download'
                               OR artifact_type IN ('dataset', 'embedded_dataset')
                        ) AS n
                    FROM artifacts
                    WHERE project_id = ANY(%s)
                      AND stage_name IS NOT NULL
                    GROUP BY project_id, stage_name
                    """,
                    (project_ids,),
                )
                artifact_count_rows = cur.fetchall()

                # Analyze stage: count UNIQUE paper stems processed, including
                # papers whose LLM said no_suitable_data (no .md was saved, so
                # they don't appear in the artifacts table). Without this the
                # "analyze-stage count" badge undercounts: TEST-101 has 10 papers but
                # only 9 analysis artifacts because Enkhee2024 was a legitimate
                # no_suitable_data — operators still expect to see "10".
                #
                # The two sources are disjoint by construction: a paper either
                # produces an analysis artifact (success path) OR generates a
                # paper_analyze_skipped event (skip path), never both within
                # the same run. UNION + COUNT(DISTINCT) also dedupes a paper
                # that flipped between paths across multiple resume runs.
                cur.execute(
                    """
                    SELECT project_id, COUNT(DISTINCT stem) AS n
                    FROM (
                        SELECT project_id,
                               replace(file_name, '.md', '') AS stem
                        FROM artifacts
                        WHERE project_id = ANY(%s)
                          AND stage_name = 'analyze'
                          AND artifact_type = 'analysis'
                          AND file_name LIKE '%%.md'
                        UNION ALL
                        SELECT project_id,
                               regexp_replace(payload->>'paper', '^.+/([^/]+)\\.pdf$', '\\1') AS stem
                        FROM execution_logs
                        WHERE project_id = ANY(%s)
                          AND event_type = 'paper_analyze_skipped'
                          AND payload->>'paper' LIKE '%%.pdf'
                    ) processed
                    GROUP BY project_id
                    """,
                    (project_ids, project_ids),
                )
                analyze_processed_rows = cur.fetchall()

        stages_by_project: Dict[str, List[Dict]] = {pid: [] for pid in project_ids}
        for row in stage_rows:
            pid = row["project_id"]
            stages_by_project.setdefault(pid, []).append(dict(row))

        artifact_counts_by_project: Dict[str, Dict[str, int]] = {pid: {} for pid in project_ids}
        for row in artifact_count_rows:
            pid = row["project_id"]
            stage_name = row["stage_name"]
            artifact_counts_by_project.setdefault(pid, {})[stage_name] = int(row["n"] or 0)

        # Overwrite raw analyze artifact count with the unique-papers-processed
        # count (artifacts + paper_analyze_skipped events).
        for row in analyze_processed_rows:
            pid = row["project_id"]
            artifact_counts_by_project.setdefault(pid, {})["analyze"] = int(row["n"] or 0)

        return (project_rows, stages_by_project, artifact_counts_by_project)

    def get_project_dashboard_stats(self, user_email: str) -> Dict[str, int]:
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT status, COUNT(*) AS n FROM projects
                    WHERE user_email = %s AND status != 'cancelled'
                    GROUP BY status
                    """,
                    (user_email,),
                )
                rows = cur.fetchall()
        counts = {r["status"]: int(r["n"]) for r in rows}
        pending = int(counts.get("pending", 0))
        running = int(counts.get("running", 0)) + int(counts.get("in_progress", 0))
        paused = int(counts.get("paused", 0))
        completed = int(counts.get("completed", 0))
        failed = int(counts.get("failed", 0))
        return {
            "pending": pending,
            "running": running,
            "paused": paused,
            "completed": completed,
            "failed": failed,
            "active": pending,
            "in_progress": running + paused,
        }

    def verify_project_ownership(self, project_id: str, user_email: str) -> bool:
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM projects WHERE id = %s AND user_email = %s",
                    (project_id, user_email),
                )
                return cur.fetchone() is not None

    def get_artifact_with_ownership(
        self, artifact_id: int, user_email: str
    ) -> Optional[Dict]:
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT a.* FROM artifacts a
                    JOIN projects p ON a.project_id = p.id
                    WHERE a.id = %s AND p.user_email = %s
                    """,
                    (artifact_id, user_email),
                )
                row = cur.fetchone()
        return dict(row) if row else None

    def update_project_status(self, project_id: str, status: str) -> None:
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE projects
                    SET status = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    """,
                    (status, project_id),
                )
            conn.commit()

    def reconcile_stale_running_after_restart(self) -> Tuple[int, int]:
        """Set orphaned running projects/stages to paused (no pipeline task after process restart).

        For every project flipped from running→paused, also write a row into
        messages and execution_logs so the UI's ProjectDetail timeline shows
        why the status changed (without an audit trail the paused state
        appears to come out of nowhere on restart, F7).
        """
        notice = (
            "Backend restarted while this pipeline was running; "
            "auto-paused. Resume to continue."
        )
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                # Capture affected project ids first so we can write the
                # explanatory rows scoped to each one.
                cur.execute(
                    "SELECT id FROM projects WHERE status = 'running'"
                )
                affected_project_ids = [row["id"] for row in cur.fetchall()]

                cur.execute(
                    """
                    UPDATE projects
                    SET status = 'paused', updated_at = CURRENT_TIMESTAMP
                    WHERE status = 'running'
                    """
                )
                projects_n = cur.rowcount

                cur.execute("UPDATE stages SET status = 'paused' WHERE status = 'running'")
                stages_n = cur.rowcount

                # Review-fix H2: also reset any artifact-level resume that was
                # in flight when the worker died.  uses an
                # in-memory `running_artifact_resumes` dict — lost on restart.
                # Without this reset the `processing` rows would remain stuck
                # until the user manually clicked again (and the compare-and-set
                # at the endpoint would still allow it, but the UX is confusing).
                #
                # Re-review fix (H2-NEW): wrap in a SAVEPOINT so an UndefinedColumn
                # error during the rolling-deploy window doesn't poison the outer
                # transaction. Without the savepoint, the bare `except: pass` would
                # leave the connection in InFailedSqlTransaction state, causing the
                # subsequent INSERT INTO messages / execution_logs to fail with
                # `psycopg.errors.InFailedSqlTransaction` — silently rolling back
                # the entire reconcile (projects + stages stay 'running').
                cur.execute("SAVEPOINT sp_artifact_reset")
                try:
                    cur.execute(
                        """
                        UPDATE artifacts
                        SET human_review_status = 'pending'
                        WHERE human_review_status = 'processing'
                        """
                    )
                    cur.execute("RELEASE SAVEPOINT sp_artifact_reset")
                except UndefinedColumn:
                    # Column not yet added on this worker (rolling deploy
                    # window). Roll back ONLY this savepoint — outer
                    # transaction (projects + stages updates) stays valid.
                    cur.execute("ROLLBACK TO SAVEPOINT sp_artifact_reset")
                    cur.execute("RELEASE SAVEPOINT sp_artifact_reset")

                for project_id in affected_project_ids:
                    cur.execute(
                        """
                        INSERT INTO messages (
                            project_id, stage_name, team_name, message_type, content
                        )
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (project_id, None, "System", "warning", notice),
                    )
                    cur.execute(
                        """
                        INSERT INTO execution_logs (
                            project_id, stage_name, team_name, event_type, message, severity, payload
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                        """,
                        (
                            project_id,
                            None,
                            "System",
                            "lifecycle.auto_paused_on_restart",
                            notice,
                            "warning",
                            json.dumps({"reason": "stale_running_reconciled"}),
                        ),
                    )
            conn.commit()
        return (projects_n, stages_n)

    def update_project_fields(
        self,
        project_id: str,
        name: Optional[str] = None,
        research_query: Optional[str] = None,
    ) -> None:
        updates: List[str] = []
        values: List = []

        if name is not None:
            updates.append("name = %s")
            values.append(name)
        if research_query is not None:
            updates.append("research_query = %s")
            values.append(research_query)

        if not updates:
            return

        updates.append("updated_at = CURRENT_TIMESTAMP")
        values.append(project_id)

        query = f"UPDATE projects SET {', '.join(updates)} WHERE id = %s"
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(query, tuple(values))
            conn.commit()

    def start_stage(self, project_id: str, stage_name: str) -> int:
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO stages (project_id, stage_name, status, start_time)
                    VALUES (%s, %s, 'running', CURRENT_TIMESTAMP)
                    RETURNING id
                    """,
                    (project_id, stage_name),
                )
                row = cur.fetchone()
                stage_id = row["id"] if row else 0
            conn.commit()
        return stage_id

    def complete_stage(
        self, stage_id: int, status: str = "completed", error_message: Optional[str] = None
    ) -> Optional[bool]:
        try:
            stage_id_int = int(stage_id)
        except Exception:
            return None
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                # When a stage finishes successfully, ensure progress
                # lands at 1.0 in the DB. Pre-fix the API layer faked the
                # 1.0 in build_project_from_db (because nothing in the
                # write path bumped it), so a fresh process boot or a
                # client that bypassed that helper saw progress=0.0 next
                # to status='completed'.
                cur.execute(
                    """
                    UPDATE stages
                    SET status = %s,
                        end_time = CURRENT_TIMESTAMP,
                        duration_seconds = (
                            CASE WHEN start_time IS NULL THEN NULL
                            ELSE (
                                EXTRACT(
                                    EPOCH FROM (CURRENT_TIMESTAMP - start_time)
                                )::INTEGER
                            )
                            END
                        ),
                        progress = CASE
                            WHEN %s = 'completed' THEN 1.0
                            ELSE progress
                        END,
                        error_message = %s
                    WHERE id = %s
                    """,
                    (status, status, error_message, stage_id_int),
                )
                updated = cur.rowcount
            conn.commit()
        return bool(updated)

    def get_project_stages(self, project_id: str) -> List[Dict]:
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM stages
                    WHERE project_id = %s
                    ORDER BY start_time ASC NULLS LAST
                    """,
                    (project_id,),
                )
                rows = cur.fetchall()
        return [dict(row) for row in rows]

    def update_stage_progress(self, stage_id: int, progress: float) -> None:
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE stages SET progress = %s WHERE id = %s",
                    (progress, stage_id),
                )
            conn.commit()

    def update_stage_progress_by_name(
        self, project_id: str, stage_name: str, progress: float
    ) -> None:
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE stages
                    SET progress = %s
                    WHERE id = (
                        SELECT id FROM stages
                        WHERE project_id = %s AND stage_name = %s
                        ORDER BY start_time DESC NULLS LAST
                        LIMIT 1
                    )
                    """,
                    (progress, project_id, stage_name),
                )
            conn.commit()

    def get_or_start_stage(
        self, project_id: str, stage_name: str
    ) -> Optional[int]:
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, status FROM stages
                    WHERE project_id = %s AND stage_name = %s
                    ORDER BY start_time DESC NULLS LAST
                    LIMIT 1
                    """,
                    (project_id, stage_name),
                )
                existing = cur.fetchone()

                if existing:
                    stage_id = existing["id"]
                    status = existing["status"]

                    if status == "completed":
                        return None
                    if status == "running":
                        return stage_id
                    if status == "paused":
                        cur.execute(
                            """
                            UPDATE stages SET status = 'running' WHERE id = %s
                            """,
                            (stage_id,),
                        )
                        conn.commit()
                        return stage_id
                    cur.execute(
                        """
                        UPDATE stages
                        SET status = 'running',
                            start_time = CURRENT_TIMESTAMP,
                            end_time = NULL,
                            error_message = NULL
                        WHERE id = %s
                        """,
                        (stage_id,),
                    )
                    conn.commit()
                    return stage_id

                cur.execute(
                    """
                    INSERT INTO stages (project_id, stage_name, status, start_time)
                    VALUES (%s, %s, 'running', CURRENT_TIMESTAMP)
                    RETURNING id
                    """,
                    (project_id, stage_name),
                )
                row = cur.fetchone()
                stage_id = row["id"] if row else 0
            conn.commit()
        return stage_id

    def pause_stage(self, project_id: str, stage_name: str) -> None:
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE stages
                    SET status = 'paused'
                    WHERE id = (
                        SELECT id FROM stages
                        WHERE project_id = %s AND stage_name = %s
                        ORDER BY start_time DESC NULLS LAST
                        LIMIT 1
                    )
                    """,
                    (project_id, stage_name),
                )
            conn.commit()

    def get_current_stage(self, project_id: str) -> Optional[Dict]:
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM stages
                    WHERE project_id = %s AND status != 'completed'
                    ORDER BY start_time DESC NULLS LAST
                    LIMIT 1
                    """,
                    (project_id,),
                )
                row = cur.fetchone()
        return dict(row) if row else None

    def update_project_overall_progress(
        self, project_id: str, overall_progress: float
    ) -> None:
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE projects
                    SET overall_progress = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    """,
                    (overall_progress, project_id),
                )
            conn.commit()

    def add_message(
        self,
        project_id: str,
        content: str,
        team_name: Optional[str] = None,
        stage_name: Optional[str] = None,
        message_type: str = "info",
    ) -> None:
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO messages (
                        project_id, stage_name, team_name, message_type, content
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (project_id, stage_name, team_name, message_type, content),
                )
            conn.commit()

    def get_project_messages(self, project_id: str, limit: int = 1000) -> List[Dict]:
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM messages
                    WHERE project_id = %s
                    ORDER BY timestamp DESC
                    LIMIT %s
                    """,
                    (project_id, limit),
                )
                rows = cur.fetchall()
        return [dict(row) for row in rows]

    def add_execution_log(
        self,
        project_id: str,
        event_type: str,
        message: str,
        severity: str = "info",
        stage_name: Optional[str] = None,
        team_name: Optional[str] = None,
        payload: Optional[Dict] = None,
    ) -> None:
        payload = payload or {}
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO execution_logs (
                        project_id, stage_name, team_name, event_type, message, severity, payload
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                    """,
                    (
                        project_id,
                        stage_name,
                        team_name,
                        event_type,
                        message,
                        severity,
                        json.dumps(payload, default=str),
                    ),
                )
            conn.commit()

    def get_execution_logs(
        self,
        project_id: str,
        stage_name: Optional[str] = None,
        limit: int = 1000,
    ) -> List[Dict]:
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                if stage_name:
                    cur.execute(
                        """
                        SELECT * FROM execution_logs
                        WHERE project_id = %s AND stage_name = %s
                        ORDER BY created_at DESC
                        LIMIT %s
                        """,
                        (project_id, stage_name, limit),
                    )
                else:
                    cur.execute(
                        """
                        SELECT * FROM execution_logs
                        WHERE project_id = %s
                        ORDER BY created_at DESC
                        LIMIT %s
                        """,
                        (project_id, limit),
                    )
                rows = cur.fetchall()
        return [dict(row) for row in rows]

    def add_artifact(
        self,
        project_id: str,
        artifact_type: str,
        file_path: str,
        stage_name: Optional[str] = None,
        storage_metadata: Optional[dict] = None,
        acquisition_source: Optional[str] = None,
        acquisition_status: Optional[str] = None,
        trust_level: Optional[str] = None,
        confidence: Optional[float] = None,
        provenance: Optional[dict] = None,
        produced_by: Optional[str] = None,
    ) -> None:
        file_size = None
        file_content = None
        file_name = os.path.basename(file_path)
        if artifact_type == "paper" and isinstance(file_path, str) and file_path.startswith(("http://", "https://")):
            parsed_path = urlparse(file_path).path or ""
            url_basename = os.path.basename(parsed_path)
            if url_basename:
                file_name = unquote(url_basename)

        storage_backend = "local"
        s3_bucket = None
        s3_key = None
        oss_bucket = None
        oss_key = None
        provenance_json = Json(provenance or {})

        if storage_metadata:
            storage_backend = storage_metadata.get("storage_backend", "local")
            s3_bucket = storage_metadata.get("s3_bucket")
            s3_key = storage_metadata.get("s3_key")
            oss_bucket = storage_metadata.get("oss_bucket")
            oss_key = storage_metadata.get("oss_key")

        if os.path.exists(file_path):
            file_size = os.path.getsize(file_path)

            try:
                ext = os.path.splitext(file_path)[1].lower()
                if ext in [".md", ".txt", ".json", ".yaml", ".yml"]:
                    with open(file_path, "r", encoding="utf-8") as f:
                        file_content = f.read()
                elif ext == ".pdf":
                    file_content = "[PDF_FILE]"
                elif ext == ".csv":
                    file_content = "[CSV_DATASET]"
            except Exception as e:
                pipeline_log(
                    f"Could not read file content for {file_path}: {e}",
                    stage="pipeline",
                    component="database",
                    level=logging.WARNING,
                )

        data_flag = None
        if (
            artifact_type == "analysis"
            and file_content
            and file_content not in ["[PDF_FILE]", "[CSV_DATASET]"]
        ):
            try:
                from .analysis_parser import AnalysisParser

                parser = AnalysisParser()
                data_flag = parser.parse_and_classify(file_content)
            except Exception as e:
                pipeline_log(
                    f"Could not parse analysis data flag for {file_path}: {e}",
                    stage="pipeline",
                    component="database",
                    level=logging.WARNING,
                )
                data_flag = "no_analysis"

        with connect(self.database_url) as conn:
            try:
                with conn.cursor() as cur:
                    try:
                        # ON CONFLICT DO UPDATE so re-analyzing a paper refreshes the
                        # artifact row instead of silently keeping the stale version.
                        # The previous DO NOTHING caused re-analyses of manual_required
                        # papers to appear "unchanged" in the DB even when MinerU+LLM
                        # produced fresh markdown on disk + a new acquisition plan.
                        # Workflow state columns (email_sent_at, human_review_status)
                        # are NOT in the UPDATE set — those follow a separate lifecycle.
                        cur.execute(
                            """
                            INSERT INTO artifacts (
                                project_id, artifact_type, file_path, file_name, file_content,
                                file_size, stage_name, data_classification_flag,
                                storage_backend, s3_bucket, s3_key, oss_bucket, oss_key,
                                acquisition_source, acquisition_status, trust_level,
                                confidence, produced_by, provenance
                            )
                            VALUES (
                                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, %s, %s
                            )
                            ON CONFLICT (project_id, artifact_type, file_path) DO UPDATE SET
                                file_name = EXCLUDED.file_name,
                                file_content = EXCLUDED.file_content,
                                file_size = EXCLUDED.file_size,
                                data_classification_flag = EXCLUDED.data_classification_flag,
                                storage_backend = EXCLUDED.storage_backend,
                                s3_bucket = EXCLUDED.s3_bucket,
                                s3_key = EXCLUDED.s3_key,
                                oss_bucket = EXCLUDED.oss_bucket,
                                oss_key = EXCLUDED.oss_key,
                                acquisition_source = EXCLUDED.acquisition_source,
                                acquisition_status = EXCLUDED.acquisition_status,
                                trust_level = EXCLUDED.trust_level,
                                confidence = EXCLUDED.confidence,
                                produced_by = EXCLUDED.produced_by,
                                provenance = EXCLUDED.provenance,
                                created_at = CURRENT_TIMESTAMP
                            """,
                            (
                                project_id,
                                artifact_type,
                                file_path,
                                file_name,
                                file_content,
                                file_size,
                                stage_name,
                                data_flag,
                                storage_backend,
                                s3_bucket,
                                s3_key,
                                oss_bucket,
                                oss_key,
                                acquisition_source,
                                acquisition_status,
                                trust_level,
                                confidence,
                                produced_by,
                                provenance_json,
                            ),
                        )
                    except UndefinedColumn:
                        conn.rollback()
                        with conn.cursor() as legacy_cur:
                            legacy_cur.execute(
                                """
                                INSERT INTO artifacts (
                                    project_id, artifact_type, file_path, file_name, file_content,
                                    file_size, stage_name, data_classification_flag,
                                    storage_backend, s3_bucket, s3_key
                                )
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                ON CONFLICT (project_id, artifact_type, file_path) DO UPDATE SET
                                    file_name = EXCLUDED.file_name,
                                    file_content = EXCLUDED.file_content,
                                    file_size = EXCLUDED.file_size,
                                    data_classification_flag = EXCLUDED.data_classification_flag,
                                    storage_backend = EXCLUDED.storage_backend,
                                    s3_bucket = EXCLUDED.s3_bucket,
                                    s3_key = EXCLUDED.s3_key,
                                    created_at = CURRENT_TIMESTAMP
                                """,
                                (
                                    project_id,
                                    artifact_type,
                                    file_path,
                                    file_name,
                                    file_content,
                                    file_size,
                                    stage_name,
                                    data_flag,
                                    storage_backend,
                                    s3_bucket,
                                    s3_key,
                                ),
                            )
                conn.commit()
            except ForeignKeyViolation as e:
                conn.rollback()
                raise ValueError(
                    f"Unknown project_id '{project_id}' for artifact insert"
                ) from e

    def get_analyze_processed_count(self, project_id: str) -> int:
        """Return the number of unique papers the analyze stage has touched
        in this project, including those that returned no_suitable_data and
        therefore left no .md artifact behind.

        Mirrors the analyze count in get_projects_page_with_summary() so the
        single-project detail view matches the project-list badge.
        """
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(DISTINCT stem) AS n
                    FROM (
                        SELECT replace(file_name, '.md', '') AS stem
                        FROM artifacts
                        WHERE project_id = %s
                          AND stage_name = 'analyze'
                          AND artifact_type = 'analysis'
                          AND file_name LIKE '%%.md'
                        UNION ALL
                        SELECT regexp_replace(payload->>'paper', '^.+/([^/]+)\\.pdf$', '\\1') AS stem
                        FROM execution_logs
                        WHERE project_id = %s
                          AND event_type = 'paper_analyze_skipped'
                          AND payload->>'paper' LIKE '%%.pdf'
                    ) processed
                    """,
                    (project_id, project_id),
                )
                row = cur.fetchone()
        if not row:
            return 0
        return int(row["n"] or 0)

    def get_paper_names_with_terminal_download(self, project_id: str) -> set:
        """Return paper stems whose download stage already produced an outcome we
        shouldn't re-attempt: completed, partial, or awaiting_external.

        Paper stem is derived from the artifact file_path's parent directory
        (datasets/<PaperName>/...). Used by the download stage to skip per-paper
        when a project resumes after a partial failure — avoids re-downloading
        what already worked while still retrying ``no_valid_dataset_files``.
        """
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT file_path
                    FROM artifacts
                    WHERE project_id = %s
                      AND artifact_type IN ('dataset', 'embedded_dataset')
                      AND acquisition_status IN ('completed', 'partial', 'awaiting_external')
                    """,
                    (project_id,),
                )
                rows = cur.fetchall()
        stems: set = set()
        for row in rows:
            path = row.get("file_path") if isinstance(row, dict) else row[0]
            if not path:
                continue
            # Path layout: .../outputs/datasets/<PaperName>/...
            parts = str(path).split("/")
            if "datasets" in parts:
                idx = parts.index("datasets")
                if idx + 1 < len(parts):
                    stems.add(parts[idx + 1])
        return stems

    def get_project_artifacts(
        self, project_id: str, artifact_type: Optional[str] = None
    ) -> List[Dict]:
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                if artifact_type:
                    cur.execute(
                        """
                        SELECT * FROM artifacts
                        WHERE project_id = %s AND artifact_type = %s
                        ORDER BY created_at DESC
                        """,
                        (project_id, artifact_type),
                    )
                else:
                    cur.execute(
                        """
                        SELECT * FROM artifacts
                        WHERE project_id = %s
                        ORDER BY created_at DESC
                        """,
                        (project_id,),
                    )
                rows = cur.fetchall()
        return [dict(row) for row in rows]

    def get_dataset_artifacts_for_qualify(self, project_id: str) -> List[Dict]:
        """project-scoped dataset rows the qualify stage operates
        on. Returns both 'dataset' and 'embedded_dataset' rows. NEVER lists the
        global datasets dir — work is strictly DB-driven and per-project."""
        sql = """
            SELECT id, project_id, artifact_type, file_path, file_name,
                   stage_name, qualification_status, oss_key, s3_key
            FROM artifacts
            WHERE project_id = %s
              AND artifact_type IN ('dataset', 'embedded_dataset')
            ORDER BY file_path ASC, id ASC
        """
        legacy_sql = """
            SELECT id, project_id, artifact_type, file_path, file_name,
                   stage_name, oss_key, s3_key
            FROM artifacts
            WHERE project_id = %s
              AND artifact_type IN ('dataset', 'embedded_dataset')
            ORDER BY file_path ASC, id ASC
        """
        with connect(self.database_url) as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(sql, (project_id,))
                    rows = cur.fetchall()
            except UndefinedColumn:
                conn.rollback()
                with conn.cursor() as legacy_cur:
                    legacy_cur.execute(legacy_sql, (project_id,))
                    rows = legacy_cur.fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d.setdefault("qualification_status", "pending")
            out.append(d)
        return out

    def set_qualification_status(
        self,
        project_id: str,
        artifact_ids: List[int],
        status: str,
        reason: Optional[str] = None,
    ) -> int:
        """write a qualify verdict to one or more artifact rows
        (a dataset unit spans N file rows; verdict is written to all members).
        Optional ``reason`` (decisive verdict explanation) is persisted alongside
        the verdict when provided; the interim ``processing`` write passes None so
        a previously-written reason is kept. Cost/token telemetry lives in
        ``stage_costs`` (see ``record_stage_cost``), not here.
        Project-scoped for safety. Returns the number of rows updated."""
        if not artifact_ids:
            return 0
        sets = ["qualification_status = %s"]
        params: List = [status]
        if reason is not None:
            sets.append("qualification_reason = %s")
            params.append((reason or "")[:2000])
        params.extend([project_id, list(artifact_ids)])
        sql = (
            "UPDATE artifacts SET " + ", ".join(sets)
            + " WHERE project_id = %s AND id = ANY(%s)"
        )
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(sql, tuple(params))
                except UndefinedColumn:
                    # qualification_reason not migrated yet — fall back to status only.
                    conn.rollback()
                    cur.execute(
                        "UPDATE artifacts SET qualification_status = %s "
                        "WHERE project_id = %s AND id = ANY(%s)",
                        (status, project_id, list(artifact_ids)),
                    )
                updated = cur.rowcount
            conn.commit()
        return updated

    def record_stage_cost(
        self, project_id: str, paper_name: str, stage: str, meta: Optional[Dict]
    ) -> None:
        """Upsert one paper's per-stage LLM cost telemetry into ``stage_costs``.
        ``meta`` keys: model / input_tokens / output_tokens / cost_usd /
        duration_ms. On conflict (same project+paper+stage, e.g. a re-run or a
        paper with multiple dataset units in qualify) the numeric fields ACCUMULATE
        so the per-paper total stays correct; the model name is refreshed."""
        if not meta or not paper_name:
            return
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        INSERT INTO stage_costs
                            (project_id, paper_name, stage, model,
                             input_tokens, output_tokens, cost_usd, duration_ms)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (project_id, paper_name, stage) DO UPDATE SET
                            model = EXCLUDED.model,
                            input_tokens = stage_costs.input_tokens + EXCLUDED.input_tokens,
                            output_tokens = stage_costs.output_tokens + EXCLUDED.output_tokens,
                            cost_usd = stage_costs.cost_usd + EXCLUDED.cost_usd,
                            duration_ms = stage_costs.duration_ms + EXCLUDED.duration_ms
                        """,
                        (
                            project_id, paper_name, stage, meta.get("model"),
                            int(meta.get("input_tokens") or 0),
                            int(meta.get("output_tokens") or 0),
                            float(meta.get("cost_usd") or 0.0),
                            int(meta.get("duration_ms") or 0),
                        ),
                    )
                    conn.commit()
                except UndefinedTable:
                    conn.rollback()  # stage_costs not migrated yet — skip silently.

    def get_paper_costs(self, project_id: str) -> List[Dict]:
        """Per-paper cost breakdown for a project: one row per (paper, stage) plus
        the data the UI needs to show a per-paper total and a stage breakdown."""
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        SELECT paper_name, stage, model, input_tokens,
                               output_tokens, cost_usd, duration_ms
                        FROM stage_costs WHERE project_id = %s
                        ORDER BY paper_name, stage
                        """,
                        (project_id,),
                    )
                    return [dict(r) for r in cur.fetchall()]
                except UndefinedTable:
                    return []

    def get_project_total_cost(self, project_id: str) -> Dict:
        """Project-wide cost rollup across ALL stages (prescreen / analyze /
        download / qualify): grand total + per-stage breakdown. Used for the
        per-project total-cost statistic."""
        empty = {
            "totalCostUsd": 0.0, "totalInputTokens": 0,
            "totalOutputTokens": 0, "totalDurationMs": 0, "byStage": {},
        }
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        SELECT stage,
                               SUM(cost_usd) AS cost_usd,
                               SUM(input_tokens) AS input_tokens,
                               SUM(output_tokens) AS output_tokens,
                               SUM(duration_ms) AS duration_ms
                        FROM stage_costs WHERE project_id = %s
                        GROUP BY stage
                        """,
                        (project_id,),
                    )
                    rows = cur.fetchall()
                except UndefinedTable:
                    return empty
        for r in rows:
            empty["byStage"][r["stage"]] = {
                "costUsd": float(r["cost_usd"] or 0.0),
                "inputTokens": int(r["input_tokens"] or 0),
                "outputTokens": int(r["output_tokens"] or 0),
                "durationMs": int(r["duration_ms"] or 0),
            }
            empty["totalCostUsd"] += float(r["cost_usd"] or 0.0)
            empty["totalInputTokens"] += int(r["input_tokens"] or 0)
            empty["totalOutputTokens"] += int(r["output_tokens"] or 0)
            empty["totalDurationMs"] += int(r["duration_ms"] or 0)
        return empty

    def get_project_artifacts_metadata(
        self, project_id: str, artifact_type: Optional[str] = None
    ) -> List[Dict]:
        with connect(self.database_url) as conn:
            try:
                with conn.cursor() as cur:
                    if artifact_type:
                        cur.execute(
                            """
                            SELECT id, project_id, artifact_type, file_path, file_name,
                                   file_size, stage_name, created_at, data_classification_flag,
                                   acquisition_source, acquisition_status, trust_level,
                                   confidence, produced_by, provenance, qualification_status,
                                   qualification_reason,
                                   CASE
                                     WHEN file_content IS NOT NULL AND file_content != ''
                                     THEN 1 ELSE 0
                                   END AS has_content
                            FROM artifacts
                            WHERE project_id = %s AND artifact_type = %s
                            ORDER BY created_at DESC
                            """,
                            (project_id, artifact_type),
                        )
                    else:
                        cur.execute(
                            """
                            SELECT id, project_id, artifact_type, file_path, file_name,
                                   file_size, stage_name, created_at, data_classification_flag,
                                   acquisition_source, acquisition_status, trust_level,
                                   confidence, produced_by, provenance, qualification_status,
                                   qualification_reason,
                                   CASE
                                     WHEN file_content IS NOT NULL AND file_content != ''
                                     THEN 1 ELSE 0
                                   END AS has_content
                            FROM artifacts
                            WHERE project_id = %s
                            ORDER BY created_at DESC
                            """,
                            (project_id,),
                        )
                    rows = cur.fetchall()
            except UndefinedColumn:
                conn.rollback()
                with conn.cursor() as legacy_cur:
                    if artifact_type:
                        legacy_cur.execute(
                            """
                            SELECT id, project_id, artifact_type, file_path, file_name,
                                   file_size, stage_name, created_at, data_classification_flag,
                                   CASE
                                     WHEN file_content IS NOT NULL AND file_content != ''
                                     THEN 1 ELSE 0
                                   END AS has_content
                            FROM artifacts
                            WHERE project_id = %s AND artifact_type = %s
                            ORDER BY created_at DESC
                            """,
                            (project_id, artifact_type),
                        )
                    else:
                        legacy_cur.execute(
                            """
                            SELECT id, project_id, artifact_type, file_path, file_name,
                                   file_size, stage_name, created_at, data_classification_flag,
                                   CASE
                                     WHEN file_content IS NOT NULL AND file_content != ''
                                     THEN 1 ELSE 0
                                   END AS has_content
                            FROM artifacts
                            WHERE project_id = %s
                            ORDER BY created_at DESC
                            """,
                            (project_id,),
                        )
                    rows = legacy_cur.fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _normalize_artifact_key(value: str) -> str:
        decoded = unquote(value or "")
        without_ext = re.sub(r"\.[^/.]+$", "", decoded, flags=re.IGNORECASE)
        return re.sub(r"[^a-z0-9]+", "", without_ext.lower())

    @staticmethod
    def _dataset_group_from_path(path: str) -> Optional[str]:
        normalized = (path or "").replace("\\", "/")
        m = re.search(r"(?:^|/)outputs/datasets/([^/]+)/", normalized, flags=re.IGNORECASE)
        if m and m.group(1):
            return m.group(1)
        m = re.search(r"(?:^|/)datasets/([^/]+)/", normalized, flags=re.IGNORECASE)
        if m and m.group(1):
            return m.group(1)
        segments = [seg for seg in normalized.split("/") if seg]
        if len(segments) >= 2:
            return segments[-2]
        return None

    @staticmethod
    def _match_analysis_to_paper(paper_file_name: str, analysis_file_name: str) -> bool:
        # Exact stem match only. The previous `startswith` rule mis-paired
        # siblings ("Yi2024.pdf" ⇒ "Yi2024_2.md"); analyses always carry their
        # paper's exact stem after stripping the optional ``_analysis`` suffix.
        paper_base = re.sub(r"\.pdf$", "", paper_file_name, flags=re.IGNORECASE)
        analysis_base = re.sub(
            r"_analysis$",
            "",
            re.sub(r"\.md$", "", analysis_file_name, flags=re.IGNORECASE),
            flags=re.IGNORECASE,
        )
        return paper_base == analysis_base

    def get_project_artifact_rows(self, project_id: str) -> List[Dict]:
        artifacts = self.get_project_artifacts_metadata(project_id)
        papers = [a for a in artifacts if a.get("artifact_type") == "paper"]
        analyses = [a for a in artifacts if a.get("artifact_type") == "analysis"]
        datasets = [
            a
            for a in artifacts
            if a.get("artifact_type") in {"dataset", "embedded_dataset", "acquisition_evidence"}
        ]

        datasets_by_key: Dict[str, List[Dict]] = {}
        for dataset in datasets:
            group = self._dataset_group_from_path(dataset.get("file_path", ""))
            key = self._normalize_artifact_key(group or "")
            if not key:
                continue
            datasets_by_key.setdefault(key, []).append(dataset)

        analysis_entries = []
        for analysis in analyses:
            file_name = os.path.basename(analysis.get("file_path", ""))
            base_name = re.sub(
                r"_analysis$",
                "",
                re.sub(r"\.md$", "", file_name, flags=re.IGNORECASE),
                flags=re.IGNORECASE,
            )
            analysis_entries.append({"analysis": analysis, "base_name": base_name})

        # Per-paper cost: group stage_costs rows by normalized paper name so each
        # paper row can carry its stage breakdown + total (analyze/download/qualify).
        costs_by_paper: Dict[str, Dict] = {}
        for c in self.get_paper_costs(project_id):
            ckey = self._normalize_artifact_key(c.get("paper_name") or "")
            if not ckey:
                continue
            entry = costs_by_paper.setdefault(
                ckey,
                {"stages": {}, "totalCostUsd": 0.0,
                 "totalInputTokens": 0, "totalOutputTokens": 0, "totalDurationMs": 0},
            )
            entry["stages"][c.get("stage")] = {
                "model": c.get("model"),
                "inputTokens": c.get("input_tokens") or 0,
                "outputTokens": c.get("output_tokens") or 0,
                "costUsd": c.get("cost_usd") or 0.0,
                "durationMs": c.get("duration_ms") or 0,
            }
            entry["totalCostUsd"] += c.get("cost_usd") or 0.0
            entry["totalInputTokens"] += c.get("input_tokens") or 0
            entry["totalOutputTokens"] += c.get("output_tokens") or 0
            entry["totalDurationMs"] += c.get("duration_ms") or 0

        used_analysis_ids = set()
        consumed_dataset_keys: set = set()
        rows: List[Dict] = []
        for paper in papers:
            paper_file_name = os.path.basename(paper.get("file_path", ""))
            paper_base = re.sub(r"\.pdf$", "", paper_file_name, flags=re.IGNORECASE)

            candidate_entries = [
                entry
                for entry in analysis_entries
                if entry["analysis"].get("id") not in used_analysis_ids
            ]
            matched_entry = next(
                (
                    entry
                    for entry in candidate_entries
                    if self._match_analysis_to_paper(
                        paper_file_name,
                        os.path.basename(entry["analysis"].get("file_path", "")),
                    )
                ),
                None,
            )
            if not matched_entry and len(papers) == 1 and len(analyses) == 1:
                matched_entry = analysis_entries[0]
            # No "closest-by-created_at" fallback. Previously this cascaded —
            # a paper without its own analysis (e.g. one that returned
            # no_suitable_data) would steal the closest analysis, shifting
            # every subsequent paper one slot and leaving the last paper
            # (often the oldest, e.g. Marc2015_2 in TEST-101) showing
            # 'pending' with an undefined match. Without that hack,
            # an analysis-less paper correctly renders as 'pending' and the
            # rest keep their exact pair.

            matched_analysis = matched_entry["analysis"] if matched_entry else None
            if matched_analysis:
                used_analysis_ids.add(matched_analysis.get("id"))

            # Two-state semantic by product decision: as long as an analysis
            # markdown artifact exists for the paper, it counts as analysed
            # ('ready'); otherwise pending. We deliberately don't expose a
            # 'failed' / 'no_data' state at this layer — downstream UIs that
            # need finer-grained outcomes consult data_classification_flag or
            # the human review queue separately.
            analysis_status = "ready" if matched_analysis else "pending"

            dataset_lookup_key = self._normalize_artifact_key(
                matched_entry["base_name"] if matched_entry else paper_base
            )
            paper_datasets = datasets_by_key.get(dataset_lookup_key, [])
            if not paper_datasets and len(papers) == 1 and len(datasets) > 0:
                paper_datasets = datasets
                consumed_dataset_keys.update(datasets_by_key.keys())
            else:
                consumed_dataset_keys.add(dataset_lookup_key)

            # Qualify verdict for the paper: highest grade across its dataset
            # units (qualify writes one verdict per unit; a paper is one unit).
            _qual_order = ("strict", "loose", "fail", "processing", "error", "pending")
            _qual_set = {
                d.get("qualification_status")
                for d in paper_datasets
                if d.get("artifact_type") in ("dataset", "embedded_dataset")
                and d.get("qualification_status")
            }
            qualification_status = next((s for s in _qual_order if s in _qual_set), None)
            # Decisive reason for the chosen verdict (from the unit that carries it).
            qualification_reason = next(
                (
                    d.get("qualification_reason")
                    for d in paper_datasets
                    if d.get("artifact_type") in ("dataset", "embedded_dataset")
                    and d.get("qualification_status") == qualification_status
                    and d.get("qualification_reason")
                ),
                None,
            )

            _cost_key = self._normalize_artifact_key(
                matched_entry["base_name"] if matched_entry else paper_base
            )
            paper_cost = costs_by_paper.get(_cost_key) or costs_by_paper.get(
                self._normalize_artifact_key(paper_base)
            )

            rows.append(
                {
                    "qualificationStatus": qualification_status,
                    "qualificationReason": qualification_reason,
                    "cost": paper_cost,
                    "paperId": paper.get("id"),
                    "paperFileName": paper_file_name,
                    "paperDisplayName": paper.get("file_name") or paper_file_name,
                    "paperFilePath": paper.get("file_path"),
                    "paperIsRemoteUrl": str(paper.get("file_path", "")).startswith(("http://", "https://")),
                    "paperFileSize": paper.get("file_size"),
                    "paperCreatedAt": paper.get("created_at"),
                    "paperHasContent": paper.get("has_content") == 1,
                    "analysisId": matched_analysis.get("id") if matched_analysis else None,
                    "analysisStatus": analysis_status,
                    "analysisFileSize": matched_analysis.get("file_size") if matched_analysis else None,
                    "analysisHasContent": matched_analysis.get("has_content") == 1 if matched_analysis else False,
                    "dataClassificationFlag": matched_analysis.get("data_classification_flag") if matched_analysis else None,
                    "datasetCount": len([d for d in paper_datasets if d.get("artifact_type") == "dataset"]),
                    "embeddedDatasetCount": len(
                        [d for d in paper_datasets if d.get("artifact_type") == "embedded_dataset"]
                    ),
                    "evidenceCount": len(
                        [d for d in paper_datasets if d.get("artifact_type") == "acquisition_evidence"]
                    ),
                    "datasets": [
                        {
                            "id": d.get("id"),
                            "fileName": d.get("file_name") or os.path.basename(d.get("file_path", "")),
                            "filePath": d.get("file_path"),
                            "fileSize": d.get("file_size"),
                            "createdAt": d.get("created_at"),
                            "artifactType": d.get("artifact_type"),
                            "acquisitionSource": d.get("acquisition_source"),
                            "acquisitionStatus": d.get("acquisition_status"),
                            "trustLevel": d.get("trust_level"),
                        }
                        for d in paper_datasets
                        if d.get("artifact_type") == "dataset"
                    ],
                    "embeddedDatasets": [
                        {
                            "id": d.get("id"),
                            "fileName": d.get("file_name") or os.path.basename(d.get("file_path", "")),
                            "filePath": d.get("file_path"),
                            "fileSize": d.get("file_size"),
                            "createdAt": d.get("created_at"),
                            "artifactType": d.get("artifact_type"),
                            "acquisitionSource": d.get("acquisition_source"),
                            "acquisitionStatus": d.get("acquisition_status"),
                            "trustLevel": d.get("trust_level"),
                        }
                        for d in paper_datasets
                        if d.get("artifact_type") == "embedded_dataset"
                    ],
                    "acquisitionEvidence": [
                        {
                            "id": d.get("id"),
                            "fileName": d.get("file_name") or os.path.basename(d.get("file_path", "")),
                            "filePath": d.get("file_path"),
                            "fileSize": d.get("file_size"),
                            "createdAt": d.get("created_at"),
                            "artifactType": d.get("artifact_type"),
                            "acquisitionSource": d.get("acquisition_source"),
                            "acquisitionStatus": d.get("acquisition_status"),
                            "trustLevel": d.get("trust_level"),
                        }
                        for d in paper_datasets
                        if d.get("artifact_type") == "acquisition_evidence"
                    ],
                }
            )

        # Dataset units not claimed by any paper (e.g. known-DB direct-retrieval
        # `_knowndb_*` units) would otherwise be invisible in the paper-centric
        # UI. Emit a standalone row per orphan group so its qualify verdict shows.
        _qual_order = ("strict", "loose", "fail", "processing", "error", "pending")

        def _sub(ds_list, atype):
            return [
                {
                    "id": d.get("id"),
                    "fileName": d.get("file_name") or os.path.basename(d.get("file_path", "")),
                    "filePath": d.get("file_path"),
                    "fileSize": d.get("file_size"),
                    "createdAt": d.get("created_at"),
                    "artifactType": d.get("artifact_type"),
                    "acquisitionSource": d.get("acquisition_source"),
                    "acquisitionStatus": d.get("acquisition_status"),
                    "trustLevel": d.get("trust_level"),
                }
                for d in ds_list
                if d.get("artifact_type") == atype
            ]

        orphan_idx = 0
        for key, ds_list in datasets_by_key.items():
            if key in consumed_dataset_keys:
                continue
            orphan_idx += 1
            qual_set = {
                d.get("qualification_status") for d in ds_list
                if d.get("artifact_type") in ("dataset", "embedded_dataset")
                and d.get("qualification_status")
            }
            status = next((s for s in _qual_order if s in qual_set), None)
            reason = next(
                (d.get("qualification_reason") for d in ds_list
                 if d.get("qualification_status") == status and d.get("qualification_reason")),
                None,
            )
            group = self._dataset_group_from_path(ds_list[0].get("file_path", "")) or key
            display = group.lstrip("/").split("/")[-1]
            if display.startswith("_knowndb_"):
                display = "Known DB: " + display[len("_knowndb_"):].replace("_", " ").upper()
            rows.append({
                "qualificationStatus": status,
                "qualificationReason": reason,
                "cost": costs_by_paper.get(key),
                # Synthetic negative id: no `paper` artifact backs this row, but
                # the UI keys rows + the dataset popover on paperId, so it must be
                # unique and non-null.
                "paperId": -orphan_idx,
                "paperFileName": display,
                "paperDisplayName": display,
                "paperFilePath": None,
                "paperIsRemoteUrl": False,
                "paperFileSize": None,
                "paperCreatedAt": ds_list[0].get("created_at"),
                "paperHasContent": False,
                "analysisId": None,
                "analysisStatus": None,
                "analysisFileSize": None,
                "analysisHasContent": False,
                "dataClassificationFlag": None,
                "datasetCount": len(_sub(ds_list, "dataset")),
                "embeddedDatasetCount": len(_sub(ds_list, "embedded_dataset")),
                "evidenceCount": len(_sub(ds_list, "acquisition_evidence")),
                "datasets": _sub(ds_list, "dataset"),
                "embeddedDatasets": _sub(ds_list, "embedded_dataset"),
                "acquisitionEvidence": _sub(ds_list, "acquisition_evidence"),
            })
        return rows

    def compute_and_update_data_flag(self, artifact_id: int) -> str:
        artifact = self.get_artifact_by_id(artifact_id)
        if not artifact or not artifact.get("file_content"):
            return "no_analysis"

        file_content = artifact["file_content"]

        if file_content in ["[PDF_FILE]", "[CSV_DATASET]"]:
            return "no_analysis"

        try:
            from .analysis_parser import AnalysisParser

            parser = AnalysisParser()
            data_flag = parser.parse_and_classify(file_content)
        except Exception as e:
            pipeline_log(
                f"Could not parse analysis data flag for artifact {artifact_id}: {e}",
                stage="pipeline",
                component="database",
                level=logging.WARNING,
            )
            data_flag = "no_analysis"

        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE artifacts
                    SET data_classification_flag = %s
                    WHERE id = %s
                    """,
                    (data_flag, artifact_id),
                )
            conn.commit()

        return data_flag

    def mark_email_sent(self, artifact_id: int) -> None:
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE artifacts
                    SET email_sent_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    """,
                    (artifact_id,),
                )
            conn.commit()

    def get_artifact_content(self, artifact_id: int) -> Optional[str]:
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT file_content FROM artifacts WHERE id = %s",
                    (artifact_id,),
                )
                row = cur.fetchone()
        return row["file_content"] if row else None

    def get_artifact_by_id(self, artifact_id: int) -> Optional[Dict]:
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM artifacts WHERE id = %s", (artifact_id,))
                row = cur.fetchone()
        return dict(row) if row else None

    def delete_artifact_by_path(self, project_id: str, file_path: str) -> int:
        """Delete artifact metadata for one project/file path and return deleted row count."""
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM artifacts
                    WHERE project_id = %s AND file_path = %s
                    """,
                    (project_id, file_path),
                )
                deleted_count = cur.rowcount
            conn.commit()
        return deleted_count

    def delete_project(self, project_id: str) -> None:
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM stages WHERE project_id = %s", (project_id,))
                cur.execute("DELETE FROM messages WHERE project_id = %s", (project_id,))
                cur.execute(
                    "DELETE FROM artifacts WHERE project_id = %s", (project_id,)
                )
                cur.execute(
                    "DELETE FROM prescreen_scores WHERE project_id = %s",
                    (project_id,),
                )
                cur.execute("DELETE FROM projects WHERE id = %s", (project_id,))
            conn.commit()

    def delete_cancelled_projects_older_than(self, days: int) -> List[str]:
        """Permanently delete cancelled projects whose updated_at is older
        than `days` days. Returns the list of deleted project ids so the
        caller can audit-log or clean up associated filesystem artifacts.

        Used by the lifespan-driven retention sweep (F4): cancel != delete
        in the UI sense, but cancelled projects are still removed after a
        configurable grace period. days <= 0 deletes immediately.
        """
        deleted: List[str] = []
        cutoff_clause = (
            "WHERE status = 'cancelled' AND updated_at < NOW() - INTERVAL '%s days'"
            if days > 0
            else "WHERE status = 'cancelled'"
        )
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                if days > 0:
                    cur.execute(
                        f"SELECT id FROM projects {cutoff_clause}",
                        (days,),
                    )
                else:
                    cur.execute(f"SELECT id FROM projects {cutoff_clause}")
                ids = [row["id"] for row in cur.fetchall()]
                for pid in ids:
                    cur.execute("DELETE FROM stages WHERE project_id = %s", (pid,))
                    cur.execute("DELETE FROM messages WHERE project_id = %s", (pid,))
                    cur.execute("DELETE FROM artifacts WHERE project_id = %s", (pid,))
                    cur.execute(
                        "DELETE FROM execution_logs WHERE project_id = %s", (pid,)
                    )
                    cur.execute(
                        "DELETE FROM prescreen_scores WHERE project_id = %s", (pid,)
                    )
                    cur.execute("DELETE FROM projects WHERE id = %s", (pid,))
                    deleted.append(pid)
            conn.commit()
        return deleted

    def has_paper_artifact_by_s2id(self, project_id: str, s2_paper_id: str) -> bool:
        """Check for an existing paper artifact using the stable Semantic Scholar paper_id."""
        if not s2_paper_id:
            return False
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        SELECT COUNT(*) AS n FROM artifacts
                        WHERE project_id = %s
                        AND artifact_type = 'paper'
                        AND provenance->>'s2_paper_id' = %s
                        """,
                        (project_id, s2_paper_id),
                    )
                    row = cur.fetchone()
                    return (row["n"] if row else 0) > 0
                except Exception:
                    return False

    def find_paper_in_other_user_projects(
        self,
        s2_paper_id: str,
        current_project_id: str,
    ) -> bool:
        """Return True iff a paper artifact with this S2 paper_id exists in
        another project owned by the **same user** as ``current_project_id``.

        Used by the Find stage to skip (without inserting a pointer row) any
        candidate that the user has already processed in a previous project —
        the new project then honestly reflects only fresh hits, and the dedup
        is scoped to one tenant rather than the whole installation.
        """
        if not s2_paper_id or not current_project_id:
            return False
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        SELECT 1 FROM artifacts a
                        JOIN projects p ON a.project_id = p.id
                        WHERE a.artifact_type = 'paper'
                          AND a.provenance->>'s2_paper_id' = %s
                          AND a.project_id <> %s
                          AND p.user_email = (
                              SELECT user_email FROM projects WHERE id = %s
                          )
                        LIMIT 1
                        """,
                        (s2_paper_id, current_project_id, current_project_id),
                    )
                    return cur.fetchone() is not None
                except Exception:
                    return False

    def has_paper_artifact_by_pmid(self, project_id: str, pmid: str) -> bool:
        """Check for an existing paper artifact using the PubMed PMID.

        PubMed-backend projects store ``provenance.pmid``
        instead of ``s2_paper_id``. Mirror of ``has_paper_artifact_by_s2id``.
        """
        if not pmid:
            return False
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        SELECT COUNT(*) AS n FROM artifacts
                        WHERE project_id = %s
                        AND artifact_type = 'paper'
                        AND provenance->>'pmid' = %s
                        """,
                        (project_id, pmid),
                    )
                    row = cur.fetchone()
                    return (row["n"] if row else 0) > 0
                except Exception:
                    return False

    def find_paper_in_other_user_projects_by_pmid(
        self,
        pmid: str,
        current_project_id: str,
    ) -> bool:
        """Same-user cross-project dedup keyed on PubMed PMID.

        Mirror of ``find_paper_in_other_user_projects`` for the
        PubMed backend. Note: this never matches against legacy S2 rows whose
        provenance carries ``s2_paper_id`` but no ``pmid`` — a one-shot Risk
        accepted in the original design (no S2 → PubMed PMID backfill).
        """
        if not pmid or not current_project_id:
            return False
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        SELECT 1 FROM artifacts a
                        JOIN projects p ON a.project_id = p.id
                        WHERE a.artifact_type = 'paper'
                          AND a.provenance->>'pmid' = %s
                          AND a.project_id <> %s
                          AND p.user_email = (
                              SELECT user_email FROM projects WHERE id = %s
                          )
                        LIMIT 1
                        """,
                        (pmid, current_project_id, current_project_id),
                    )
                    return cur.fetchone() is not None
                except Exception:
                    return False

    def has_paper_artifacts_by_pmids(
        self, project_id: str, pmids: List[str]
    ) -> set:
        """Batch variant of ``has_paper_artifact_by_pmid`` — returns the
        subset of ``pmids`` already present as paper artifacts in
        ``project_id``. Used by Find stage to fold N per-paper SELECTs
        into one SQL round-trip when ranking large candidate pools.
        """
        if not pmids:
            return set()
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        SELECT DISTINCT provenance->>'pmid' AS pmid
                        FROM artifacts
                        WHERE project_id = %s
                          AND artifact_type = 'paper'
                          AND provenance->>'pmid' = ANY(%s)
                        """,
                        (project_id, list(pmids)),
                    )
                    return {row["pmid"] for row in cur.fetchall() if row.get("pmid")}
                except Exception:
                    return set()

    def has_paper_artifacts_by_s2ids(
        self, project_id: str, s2_paper_ids: List[str]
    ) -> set:
        """Batch variant of ``has_paper_artifact_by_s2id``. Mirrors
        ``has_paper_artifacts_by_pmids`` for the legacy S2 backend."""
        if not s2_paper_ids:
            return set()
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        SELECT DISTINCT provenance->>'s2_paper_id' AS pid
                        FROM artifacts
                        WHERE project_id = %s
                          AND artifact_type = 'paper'
                          AND provenance->>'s2_paper_id' = ANY(%s)
                        """,
                        (project_id, list(s2_paper_ids)),
                    )
                    return {row["pid"] for row in cur.fetchall() if row.get("pid")}
                except Exception:
                    return set()

    def find_papers_in_other_user_projects_by_pmids(
        self, pmids: List[str], current_project_id: str
    ) -> set:
        """Batch variant of ``find_paper_in_other_user_projects_by_pmid`` —
        returns the subset of ``pmids`` already present as paper artifacts
        in the SAME user's OTHER projects (cross-project dedup). One SQL
        instead of N. Tenant isolation preserved via user_email JOIN.
        """
        if not pmids or not current_project_id:
            return set()
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        SELECT DISTINCT a.provenance->>'pmid' AS pmid
                        FROM artifacts a
                        JOIN projects p ON a.project_id = p.id
                        WHERE a.artifact_type = 'paper'
                          AND a.provenance->>'pmid' = ANY(%s)
                          AND a.project_id <> %s
                          AND p.user_email = (
                              SELECT user_email FROM projects WHERE id = %s
                          )
                        """,
                        (list(pmids), current_project_id, current_project_id),
                    )
                    return {row["pmid"] for row in cur.fetchall() if row.get("pmid")}
                except Exception:
                    return set()

    def find_papers_in_other_user_projects_by_s2ids(
        self, s2_paper_ids: List[str], current_project_id: str
    ) -> set:
        """Batch variant for legacy S2 backend; mirrors the PMID version."""
        if not s2_paper_ids or not current_project_id:
            return set()
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        SELECT DISTINCT a.provenance->>'s2_paper_id' AS pid
                        FROM artifacts a
                        JOIN projects p ON a.project_id = p.id
                        WHERE a.artifact_type = 'paper'
                          AND a.provenance->>'s2_paper_id' = ANY(%s)
                          AND a.project_id <> %s
                          AND p.user_email = (
                              SELECT user_email FROM projects WHERE id = %s
                          )
                        """,
                        (list(s2_paper_ids), current_project_id, current_project_id),
                    )
                    return {row["pid"] for row in cur.fetchall() if row.get("pid")}
                except Exception:
                    return set()

    def find_paper_path_by_s2id(self, s2_paper_id: str) -> Optional[str]:
        """Return the file_path of an existing paper artifact across ALL projects, or None."""
        if not s2_paper_id:
            return None
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        SELECT file_path FROM artifacts
                        WHERE artifact_type = 'paper'
                        AND provenance->>'s2_paper_id' = %s
                        LIMIT 1
                        """,
                        (s2_paper_id,),
                    )
                    row = cur.fetchone()
                    return row["file_path"] if row else None
                except Exception:
                    return None

    def find_reusable_analyses_by_stems(
        self,
        exclude_project_id: str,
        paper_stems: List[str],
    ) -> Dict[str, Dict[str, Any]]:
        """For each paper stem, find a reusable analysis artifact from another
        project **owned by the same user** (matched on
        file_name = ``<stem>_analysis.md``). Returns the most recent match
        per stem.

        Tenant isolation: analyses produced by other users are never returned —
        the JOIN with ``projects`` restricts results to the owner of
        ``exclude_project_id``. This mirrors the same-user dedup model used
        by Find (see ``find_paper_in_other_user_projects``) and avoids
        granting one user implicit read access to another user's analyses
        via the per-project download/file endpoints.

        The mapping is ``{stem → {file_path, source_project_id}}``. The caller
        must still verify on-disk existence and is responsible for inserting
        a new artifact row scoped to the current project.

        Used by the analyze stage to skip MinerU+LLM re-runs when an earlier
        project has already produced the analysis for the same paper file.
        """
        if not paper_stems:
            return {}
        target_names = [f"{stem}_analysis.md" for stem in paper_stems]
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT ON (a.file_name)
                           a.file_name, a.file_path, a.project_id,
                           a.data_classification_flag
                    FROM artifacts a
                    JOIN projects p ON a.project_id = p.id
                    WHERE a.artifact_type = 'analysis'
                      AND a.project_id <> %s
                      AND a.file_name = ANY(%s)
                      AND p.user_email = (
                          SELECT user_email FROM projects WHERE id = %s
                      )
                    ORDER BY a.file_name, a.created_at DESC
                    """,
                    (exclude_project_id, target_names, exclude_project_id),
                )
                rows = cur.fetchall()
        out: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            fn = row["file_name"] or ""
            if fn.endswith("_analysis.md"):
                stem = fn[: -len("_analysis.md")]
            else:
                stem = os.path.splitext(fn)[0]
            out[stem] = {
                "file_path": row["file_path"],
                "source_project_id": row["project_id"],
                "data_classification_flag": row.get("data_classification_flag"),
            }
        return out

    def has_paper_artifact(self, project_id: str, paper_identifier: str) -> bool:
        normalized = paper_identifier.replace(".pdf", "")
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*) AS n FROM artifacts
                    WHERE project_id = %s
                    AND artifact_type = 'paper'
                    AND (file_path LIKE %s OR file_name LIKE %s)
                    """,
                    (project_id, f"%{normalized}%", f"%{normalized}%"),
                )
                row = cur.fetchone()
        return (row["n"] if row else 0) > 0

    def has_analysis_artifact(self, project_id: str, paper_identifier: str) -> bool:
        normalized = (
            paper_identifier.replace(".pdf", "").replace(".md", "")
        )
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*) AS n FROM artifacts
                    WHERE project_id = %s
                    AND artifact_type = 'analysis'
                    AND (file_path LIKE %s OR file_name LIKE %s)
                    """,
                    (project_id, f"%{normalized}%", f"%{normalized}%"),
                )
                row = cur.fetchone()
        return (row["n"] if row else 0) > 0

    def has_dataset_artifact(self, project_id: str, paper_identifier: str) -> bool:
        normalized = (
            paper_identifier.replace(".pdf", "").replace(".md", "")
        )
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*) AS n FROM artifacts
                    WHERE project_id = %s
                    AND artifact_type = 'dataset'
                    AND file_path LIKE %s
                    """,
                    (project_id, f"%{normalized}%"),
                )
                row = cur.fetchone()
        return (row["n"] if row else 0) > 0

    def get_completed_paper_identifiers(
        self, project_id: str, artifact_type: str
    ) -> List[str]:
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT file_path FROM artifacts
                    WHERE project_id = %s AND artifact_type = %s
                    """,
                    (project_id, artifact_type),
                )
                rows = cur.fetchall()

        identifiers: List[str] = []
        for row in rows:
            file_path = row["file_path"]
            base_name = os.path.splitext(os.path.basename(file_path))[0]
            if base_name and base_name not in identifiers:
                identifiers.append(base_name)

        return identifiers

    # ------------------------------------------------------------------
    # Global papers + analysis_cache
    # ------------------------------------------------------------------

    def get_paper_pmid_by_path(
        self, project_id: str, file_path: str
    ) -> Optional[str]:
        """Look up the PMID stored in `provenance.pmid` for the artifact
        matching ``(project_id, file_path)``. lets analyze
        stage retrieve PMID from a candidate paper's file path so cache
        lookup can fire without a separate plumbing channel.
        """
        if not project_id or not file_path:
            return None
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        SELECT provenance->>'pmid' AS pmid FROM artifacts
                        WHERE project_id = %s
                          AND artifact_type = 'paper'
                          AND file_path = %s
                        LIMIT 1
                        """,
                        (project_id, file_path),
                    )
                    row = cur.fetchone()
                    return row.get("pmid") if row else None
                except Exception:
                    return None

    def find_user_already_seen_pmids(
        self, user_email: str, pmids: List[str]
    ) -> set:
        """Return the subset of `pmids` already present in any paper artifact
        owned by ``user_email`` (across all that user's projects).

        Single-shot ``WHERE pmid = ANY(%s)`` query — replaces what would
        otherwise be N per-PMID round-trips in the find-stage fill-to-target
        loop.
        """
        if not user_email or not pmids:
            return set()
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        SELECT DISTINCT a.provenance->>'pmid' AS pmid
                        FROM artifacts a
                        JOIN projects p ON a.project_id = p.id
                        WHERE a.artifact_type = 'paper'
                          AND p.user_email = %s
                          AND a.provenance->>'pmid' = ANY(%s)
                        """,
                        (user_email, list(pmids)),
                    )
                    return {row["pmid"] for row in cur.fetchall() if row.get("pmid")}
                except Exception:
                    return set()

    def upsert_paper(
        self,
        pmid: str,
        doi: Optional[str] = None,
        pmcid: Optional[str] = None,
        title: Optional[str] = None,
        authors: Optional[List[str]] = None,
        publication_date: Optional[str] = None,
        journal: Optional[str] = None,
    ) -> None:
        """Idempotent insert into the global `papers` table.

        keyed on PMID. Called by find/team.py after a paper is
        added to artifacts so the analysis_cache FK to papers(pmid) is always
        satisfied. ON CONFLICT DO NOTHING — first writer wins, subsequent
        callers leave existing metadata alone.

        Round 4 review fix: wrapped in UndefinedTable defensive try/except so
        rolling deploys where init_schema hasn't yet run on every worker
        silently no-op instead of crashing the pipeline.
        """
        if not pmid:
            return
        authors_json: Optional[str] = json.dumps(authors) if authors else None
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        INSERT INTO papers (
                            pmid, doi, pmcid, title, authors, publication_date, journal
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (pmid) DO NOTHING
                        """,
                        (pmid, doi, pmcid, title, authors_json, publication_date, journal),
                    )
                    conn.commit()
                except UndefinedTable:
                    conn.rollback()  # init_schema not yet run on this worker
                except Exception:
                    conn.rollback()
                    raise

    def record_prescreen_score(
        self,
        *,
        project_id: str,
        paper_id: str,
        prescreen_score: Optional[float],
        prescreen_model: Optional[str],
        threshold: Optional[float],
        decision: str,
    ) -> None:
        """persist a prescreen score for recall audit.

        Keyed (project_id, paper_id). Written at score time for EVERY scored
        paper — passed, filtered-out, or error — so recall verification can
        inspect why each paper passed/failed. ON CONFLICT upsert makes
        project re-runs / wide-net re-scoring update the row rather than
        erroring or silently keeping the stale score.

        Wrapped in UndefinedTable defensive try/except for rolling deploys
        where init_schema hasn't yet run on every worker.
        """
        if not project_id or not paper_id:
            return
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        INSERT INTO prescreen_scores (
                            project_id, paper_id, prescreen_score,
                            prescreen_model, threshold, decision
                        )
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (project_id, paper_id) DO UPDATE SET
                            prescreen_score = EXCLUDED.prescreen_score,
                            prescreen_model = EXCLUDED.prescreen_model,
                            threshold = EXCLUDED.threshold,
                            decision = EXCLUDED.decision,
                            scored_at = CURRENT_TIMESTAMP
                        """,
                        (
                            project_id, paper_id, prescreen_score,
                            prescreen_model, threshold, decision,
                        ),
                    )
                    conn.commit()
                except UndefinedTable:
                    conn.rollback()  # init_schema not yet run on this worker
                except Exception:
                    conn.rollback()
                    raise

    def get_prescreen_scores(self, project_id: str) -> List[Dict]:
        """read all prescreen scores for a project (recall audit)."""
        if not project_id:
            return []
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        SELECT project_id, paper_id, prescreen_score,
                               prescreen_model, threshold, decision, scored_at
                        FROM prescreen_scores
                        WHERE project_id = %s
                        ORDER BY prescreen_score DESC NULLS LAST
                        """,
                        (project_id,),
                    )
                    rows = cur.fetchall()
                except UndefinedTable:
                    conn.rollback()
                    return []
        # connect() uses a dict_row factory, so each row is already a dict.
        return [dict(r) for r in rows]

    def get_cached_analysis(
        self,
        pmid: str,
        model_name: str,
        prompt_hash: str,
        content_hash: str,
    ) -> Optional[Dict]:
        """Look up a global cached analysis for the given cache key.

        PRIVACY: the response dict is STRICTLY limited to
        the cache content + key columns — NEVER includes user_email,
        project_id, or any owner identifier. The schema has no such columns
        either, so leak-by-accident is structurally impossible.

        Returns None on miss / on UndefinedTable (rolling deploy window).
        """
        if not pmid:
            return None
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        SELECT analysis_markdown,
                               plan_json,
                               data_classification_flag,
                               created_at
                        FROM analysis_cache
                        WHERE pmid = %s
                          AND model_name = %s
                          AND prompt_hash = %s
                          AND content_hash = %s
                        ORDER BY created_at DESC
                        LIMIT 1
                        """,
                        (pmid, model_name, prompt_hash, content_hash),
                    )
                    row = cur.fetchone()
                    if not row:
                        return None
                    # Explicit dict construction — never re-export row keys
                    # we didn't intend to expose (e.g. internal id).
                    return {
                        "analysis_markdown": row["analysis_markdown"],
                        "plan_json": row["plan_json"],
                        "data_classification_flag": row["data_classification_flag"],
                        "created_at": row["created_at"],
                    }
                except UndefinedTable:
                    return None

    def insert_analysis_cache(
        self,
        pmid: str,
        model_name: str,
        prompt_hash: str,
        content_hash: str,
        analysis_markdown: str,
        plan_json: Optional[Dict] = None,
        data_classification_flag: Optional[str] = None,
    ) -> None:
        """Write an analysis_cache row, refreshing on key collision.

        ON CONFLICT DO UPDATE on the UNIQUE composite key: a re-analysis
        that hashes to the same (pmid, model, prompt_hash, content_hash)
        overwrites the cached markdown, plan, and classification flag with
        the latest LLM output. This is "last-writer-wins" semantics rather
        than "first-writer-wins" because:
          1. The RETRY_CLASSIFICATIONS bypass in analyze/team.py deliberately
             re-runs papers whose cached flag is manual_required /
             contact_author / no_analysis, expecting a better result.
          2. MinerU/pdfplumber typically produce byte-identical text for a
             given PDF, so cache_key collisions are the norm even when the
             upstream extractor changed (e.g. quota-throttled fallback).
        Concurrent writers from different projects converge to the most
        recent value; rare LLM nondeterminism is tolerable.

         Stores no owner info. Review fix:
        UndefinedTable wrapped for rolling-deploy safety.
        """
        if not pmid:
            return
        from psycopg.types.json import Json
        plan_payload = Json(plan_json) if plan_json is not None else None
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                try:
                    # ON CONFLICT DO UPDATE rather than DO NOTHING so a re-analysis
                    # (e.g. the RETRY_CLASSIFICATIONS bypass at analyze/team.py) that
                    # hashes to the same key — typically when MinerU produces identical
                    # extraction text and the prompt template version is unchanged —
                    # still overwrites the cached row with the newer LLM output. The
                    # composite key collides because content is byte-identical; the
                    # value (markdown + plan + flag) can still differ due to LLM
                    # nondeterminism or a previous run hitting a degraded path.
                    cur.execute(
                        """
                        INSERT INTO analysis_cache (
                            pmid, model_name, prompt_hash, content_hash,
                            analysis_markdown, plan_json, data_classification_flag
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (pmid, model_name, prompt_hash, content_hash) DO UPDATE SET
                            analysis_markdown = EXCLUDED.analysis_markdown,
                            plan_json = EXCLUDED.plan_json,
                            data_classification_flag = EXCLUDED.data_classification_flag,
                            created_at = CURRENT_TIMESTAMP
                        """,
                        (pmid, model_name, prompt_hash, content_hash,
                         analysis_markdown, plan_payload, data_classification_flag),
                    )
                    conn.commit()
                except UndefinedTable:
                    conn.rollback()  # init_schema not yet run on this worker
                except ForeignKeyViolation:
                    conn.rollback()  # papers row missing — bootstrap path race
                except Exception:
                    conn.rollback()
                    raise

    def get_cached_prescreen_scores(
        self,
        model_name: str,
        prompt_hash: str,
        paper_content_hashes: Dict[str, str],
    ) -> Dict[str, Dict]:
        """Batch cross-project pre-screen cache lookup.

        ``paper_content_hashes`` maps paper_id → content_hash. Returns
        {paper_id: {"score": float, "reason": str|None}} only for rows whose
        (paper_id, model_name, prompt_hash, content_hash) ALL match. Empty on
        miss / UndefinedTable (rolling deploy). Global cache — no owner info.
        """
        if not paper_content_hashes:
            return {}
        paper_ids = list(paper_content_hashes.keys())
        out: Dict[str, Dict] = {}
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        SELECT paper_id, content_hash, prescreen_score, prescreen_reason
                        FROM prescreen_cache
                        WHERE model_name = %s
                          AND prompt_hash = %s
                          AND paper_id = ANY(%s)
                        """,
                        (model_name, prompt_hash, paper_ids),
                    )
                    for row in cur.fetchall():
                        pid = row["paper_id"]
                        if paper_content_hashes.get(pid) == row["content_hash"]:
                            out[pid] = {
                                "score": row["prescreen_score"],
                                "reason": row["prescreen_reason"],
                            }
                except UndefinedTable:
                    return {}
        return out

    def insert_prescreen_cache(self, rows: List[Dict]) -> None:
        """Batch upsert pre-screen cache rows.

        Each row needs paper_id, model_name, prompt_hash, content_hash,
        prescreen_score; prescreen_reason optional. Rows without a paper_id or
        a numeric score are skipped (errors are never cached). Global asset —
        stores no owner info. ON CONFLICT refreshes the cached score.
        """
        if not rows:
            return
        params = [
            (
                r["paper_id"], r["model_name"], r["prompt_hash"], r["content_hash"],
                r["prescreen_score"], r.get("prescreen_reason"),
            )
            for r in rows
            if r.get("paper_id") and r.get("prescreen_score") is not None
        ]
        if not params:
            return
        with connect(self.database_url) as conn:
            with conn.cursor() as cur:
                try:
                    cur.executemany(
                        """
                        INSERT INTO prescreen_cache (
                            paper_id, model_name, prompt_hash, content_hash,
                            prescreen_score, prescreen_reason
                        )
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (paper_id, model_name, prompt_hash, content_hash) DO UPDATE SET
                            prescreen_score = EXCLUDED.prescreen_score,
                            prescreen_reason = EXCLUDED.prescreen_reason,
                            created_at = CURRENT_TIMESTAMP
                        """,
                        params,
                    )
                    conn.commit()
                except UndefinedTable:
                    conn.rollback()  # init_schema not yet run on this worker
                except Exception:
                    conn.rollback()
                    raise

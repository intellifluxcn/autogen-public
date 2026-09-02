"""Register canonical-DB E+R as dataset artifacts.

Two entry points sharing one per-resource routine (``ingest_resource``):
- ``_maybe_fetch_known_db`` (orchestrator) ingests only the DBs a project's
  papers reference — the paper-coupled path.
- ``ingest_all_known_db`` ingests EVERY registered DB regardless of papers — the
  decoupled "direct ingest": strict E+R lives in a small fixed set of canonical
  DBs, so you can pull them straight without a paper search. Run via
  ``python -m download.known_db_ingest <project_id>`` (also runs qualify).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from download.known_db_fetch import fetch_resource_datasets
from utils.pipeline_log import pipeline_log

logger = logging.getLogger(__name__)


def _unit_name(resource: Dict) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", resource["name"].lower()).strip("_")
    return f"_knowndb_{slug}"


def _expected_paths(resource: Dict) -> List[str]:
    """The artifact file_paths a fully-ingested resource would register."""
    unit = _unit_name(resource)
    return [str(Path("outputs/datasets") / unit / s["filename"])
            for s in resource.get("datasets", [])]


def ingest_resource(
    dao, project_id: str, resource: Dict, *, team: str = "PipelineRunner",
    existing_paths: Optional[set] = None,
) -> int:
    """Fetch one resource's E/R/linkage files, upload to storage, register them
    as project dataset artifacts. Returns the number of files registered.
    Best-effort per file; never raises. Files already registered for this
    project (``existing_paths``) are skipped — no re-upload, no duplicate row."""
    from download.artifact_finalize import _upload_with_retries
    from storage import get_storage

    unit = _unit_name(resource)
    dest_dir = Path("outputs/datasets") / unit
    files = fetch_resource_datasets(resource, dest_dir, project_id=project_id, team=team)
    roles = {s["filename"]: s["role"] for s in resource.get("datasets", [])}
    seen = existing_paths or set()
    registered = 0
    for f in files:
        if str(f) in seen:
            continue
        storage_kwargs: Dict[str, Any] = {}
        try:
            result = _upload_with_retries(
                storage=get_storage(), local_path=str(f),
                key=f"datasets/{unit}/{f.name}", team_name=team, project_id=project_id,
            )
            storage_kwargs = {"storage_metadata": {
                "storage_backend": result.storage_backend,
                "s3_bucket": result.s3_bucket, "s3_key": result.s3_key,
                "oss_bucket": result.oss_bucket, "oss_key": result.oss_key,
            }}
        except Exception as e:
            pipeline_log(
                f"known_db: upload failed for {f.name}, recording local-only: {e}",
                stage="download", team=team, project_id=project_id, level=logging.WARNING,
            )
        dao.add_artifact(
            project_id, "dataset", str(f), stage_name="download",
            acquisition_source="known_db_retrieval", acquisition_status="completed",
            trust_level="medium", confidence=0.8, produced_by="known_db_fetch",
            provenance={"resource": resource["name"], "role": roles.get(f.name)},
            **storage_kwargs,
        )
        registered += 1
    pipeline_log(
        f"known_db: registered {registered} files for {resource['name']} ({unit})",
        stage="download", team=team, project_id=project_id,
    )
    return registered


def fetchable_resources() -> List[Dict]:
    """All registered resources that have direct-download endpoints."""
    from analyze.known_resources import KNOWN_RESOURCES
    return [r for r in KNOWN_RESOURCES if r.get("datasets")]


def ingest_all_known_db(dao, project_id: str, *, team: str = "PipelineRunner") -> int:
    """Direct ingest: register every fetchable canonical DB into the project,
    independent of any paper. Idempotent — a resource whose files are already
    registered is skipped (no re-download, no re-upload, no duplicate row).
    Returns the number of NEWLY registered files."""
    total = 0
    resources = fetchable_resources()
    existing = {a.get("file_path") for a in dao.get_project_artifacts(project_id, "dataset")}
    pipeline_log(
        f"known_db: direct ingest of {len(resources)} canonical DB(s): "
        f"{[r['name'] for r in resources]}",
        stage="download", team=team, project_id=project_id,
    )
    for resource in resources:
        expected = _expected_paths(resource)
        if expected and all(p in existing for p in expected):
            pipeline_log(
                f"known_db: {resource['name']} already fully registered — skip",
                stage="download", team=team, project_id=project_id,
            )
            continue
        try:
            n = ingest_resource(dao, project_id, resource, team=team, existing_paths=existing)
            existing.update(_expected_paths(resource))
            total += n
        except Exception as e:
            pipeline_log(
                f"known_db: ingest failed for {resource['name']}: {type(e).__name__}: {e}",
                stage="download", team=team, project_id=project_id, level=logging.WARNING,
            )
    return total


DIRECT_INGEST_PROJECT_NAME = "KNOWN-DB-DIRECT"


def get_or_create_direct_ingest_project(dao) -> str:
    """Return the single project that holds the decoupled known-DB direct
    ingest, creating it only if absent. Reused across runs so the (fixed,
    version-pinned) canonical DBs are ingested once, not duplicated per run.

    Always kept at ``status='completed'`` on purpose: this project has no paper
    search and must NOT be run through the standard find→analyze→download
    pipeline. 'completed' keeps it out of the restart reconciler (which only
    touches 'running') and out of resume (allowed only from paused/error), so
    it is never auto-executed — a reused project left 'paused'/'running' by an
    older run is normalized back to 'completed' here."""
    existing = dao.get_project_by_name(DIRECT_INGEST_PROJECT_NAME)
    if existing:
        if existing.get("status") != "completed":
            dao.update_project_status(existing["id"], "completed")
        return existing["id"]
    return dao.create_project(
        name=DIRECT_INGEST_PROJECT_NAME,
        research_query="(known-DB direct ingest; no paper search)",
        status="completed",
    )


async def ingest_and_qualify() -> Dict[str, int]:
    """One-shot: reuse (or create) the dedicated non-runnable project, ingest
    any not-yet-registered canonical DBs into it, then qualify. Idempotent —
    re-running when everything is already ingested + verified registers nothing
    and re-charges no qualify calls. Returns the qualify verdict counts."""
    from database.dao import ProjectDAO
    from qualify.team import QualifyTeam

    dao = ProjectDAO()
    project_id = get_or_create_direct_ingest_project(dao)
    logger.info("known_db: direct-ingest project %s", project_id)
    n = ingest_all_known_db(dao, project_id)
    logger.info("known_db direct ingest: %d new file(s) registered; running qualify", n)
    counts = await QualifyTeam(team_name="KnownDBIngest", project_id=project_id).run()
    return counts or {}


if __name__ == "__main__":
    import asyncio
    import sys

    if len(sys.argv) > 1:
        print("usage: python -m download.known_db_ingest")
        raise SystemExit(2)
    result = asyncio.run(ingest_and_qualify())
    print(f"qualify verdicts: {result}")

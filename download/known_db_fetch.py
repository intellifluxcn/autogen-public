"""Direct retrieval of canonical E+R from known pharmacogenomics DBs.

Single-paper auto-acquisition of a linkable E+R is structurally near-impossible
(researchers deposit E in GEO, R in figures/supplements/external DBs). The only
stable source of linkable E+R is the canonical DBs (GDSC, CCLE/DepMap, PRISM),
whose data files ARE reachable on stable endpoints (figshare ndownloader, Sanger
object store) even though their JS portal pages are dead. When analyze detects
such a resource, fetch the canonical E+R directly instead of only emitting human
guidance.

Opt-in via KNOWN_DB_FETCH_ENABLED (default off): these are large shared files
(hundreds of MB), so the caller fetches once per project, not per paper.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Dict, List

import requests

from utils.pipeline_log import pipeline_log

logger = logging.getLogger(__name__)

# url -> first local path fetched this process. Lets a file shared across DBs
# (e.g. CCLE_expression reused by both CCLE/DepMap and GDSC units) download once
# and hardlink into the other unit dirs instead of re-downloading hundreds of MB.
_FETCH_CACHE: Dict[str, str] = {}

KNOWN_DB_FETCH_TIMEOUT = int(os.getenv("KNOWN_DB_FETCH_TIMEOUT", "900"))
# Per-file cap (these matrices are big — CCLE expression ~430MB); skip anything
# larger so a mislabeled giant file can't blow up disk.
KNOWN_DB_MAX_BYTES = int(os.getenv("KNOWN_DB_MAX_BYTES", str(1200 * 1024 * 1024)))
_CHUNK = 1024 * 1024


def known_db_fetch_enabled() -> bool:
    return os.getenv("KNOWN_DB_FETCH_ENABLED", "false").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _download_stream(url: str, dest: Path, *, timeout: int, max_bytes: int) -> bool:
    """Stream ``url`` to ``dest`` with a byte cap. Returns True on success.
    Best-effort: logs and returns False on any failure (never raises)."""
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with requests.get(url, stream=True, timeout=timeout, allow_redirects=True) as resp:
            resp.raise_for_status()
            written = 0
            with open(tmp, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=_CHUNK):
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > max_bytes:
                        raise ValueError(f"exceeds {max_bytes} byte cap")
                    fh.write(chunk)
        if written == 0:
            raise ValueError("empty response")
        os.replace(tmp, dest)
        return True
    except Exception as e:
        logger.warning("known_db: download failed %s -> %s: %s", url, dest, e)
        with __import__("contextlib").suppress(Exception):
            tmp.unlink()
        return False


def fetch_resource_datasets(
    resource: Dict,
    dest_dir: Path,
    *,
    project_id: str = None,
    team: str = None,
) -> List[Path]:
    """Download a resource's registered E/R/linkage files into ``dest_dir``.

    Idempotent: a file already present (non-empty) is reused, not re-downloaded
    — so a re-run or a second project sharing the dir is cheap. Returns the list
    of materialized file paths (only those that succeeded)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    out: List[Path] = []
    for spec in resource.get("datasets") or []:
        dest = dest_dir / spec["filename"]
        if dest.exists() and dest.stat().st_size > 0:
            out.append(dest)
            continue
        # Reuse a file already fetched this run (shared across DBs) via hardlink.
        cached = _FETCH_CACHE.get(spec["url"])
        if cached and os.path.exists(cached) and os.path.getsize(cached) > 0:
            try:
                os.link(cached, dest)
            except OSError:
                try:
                    shutil.copyfile(cached, dest)
                except OSError:
                    cached = None
            if cached:
                pipeline_log(
                    f"known_db: reused cached {spec['filename']} for {resource['name']}",
                    stage="download", team=team, project_id=project_id,
                )
                out.append(dest)
                continue
        pipeline_log(
            f"known_db: fetching {resource['name']} {spec['role']} -> {spec['filename']}",
            stage="download", team=team, project_id=project_id,
        )
        if _download_stream(
            spec["url"], dest, timeout=KNOWN_DB_FETCH_TIMEOUT, max_bytes=KNOWN_DB_MAX_BYTES
        ):
            _FETCH_CACHE[spec["url"]] = str(dest)
            out.append(dest)
    return out

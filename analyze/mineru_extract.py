"""MinerU /api/v4 local upload -> markdown (full.md) extraction."""

from __future__ import annotations

import asyncio
import io
import logging
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
from aiohttp import hdrs
from utils.pipeline_log import pipeline_log

logger = logging.getLogger(__name__)

MINERU_DEFAULT_BASE = "https://mineru.net"


class MinerUExtractError(Exception):
    pass


def _find_full_md(root: Path) -> Optional[Path]:
    matches = list(root.rglob("full.md"))
    if not matches:
        return None
    return matches[0]


async def _post_json(
    session: aiohttp.ClientSession, url: str, headers: dict, payload: dict
) -> Tuple[int, Dict[str, Any]]:
    async with session.post(url, json=payload, headers=headers) as resp:
        body = await resp.json(content_type=None)
        return resp.status, body if isinstance(body, dict) else {"raw": body}


async def _put_file(session: aiohttp.ClientSession, upload_url: str, data: bytes) -> int:
    # Presigned OSS/S3 URLs reject requests if Content-Type was not part of the signature.
    # MinerU docs: do not set Content-Type on upload.
    async with session.put(
        upload_url,
        data=data,
        skip_auto_headers={hdrs.CONTENT_TYPE},
    ) as resp:
        status = resp.status
        if status not in (200, 201, 204):
            try:
                err = (await resp.text())[:800]
            except Exception:
                err = ""
            if err.strip():
                pipeline_log(
                    f"MinerU upload error body: {err}",
                    stage="analyze",
                    component="mineru",
                    level=logging.WARNING,
                )
        return status


async def _get_json(
    session: aiohttp.ClientSession, url: str, headers: dict
) -> Tuple[int, Dict[str, Any]]:
    async with session.get(url, headers=headers) as resp:
        body = await resp.json(content_type=None)
        return resp.status, body if isinstance(body, dict) else {"raw": body}


async def _download_zip(session: aiohttp.ClientSession, zip_url: str) -> bytes:
    async with session.get(zip_url) as resp:
        resp.raise_for_status()
        return await resp.read()


def _terminal_states() -> set:
    return {"done", "failed"}


def _all_results_done(results: List[Dict[str, Any]]) -> bool:
    if not results:
        return False
    for item in results:
        state = (item.get("state") or "").lower()
        if state in _terminal_states():
            continue
        return False
    return True


async def extract_markdown_from_pdf_mineru(
    pdf_path: str,
    api_token: str,
    *,
    base_url: str = MINERU_DEFAULT_BASE,
    model_version: str = "pipeline",
    language: str = "en",
    timeout_seconds: float = 600.0,
    poll_interval_seconds: float = 2.0,
) -> Optional[Tuple[str, Path, Path]]:
    """
    Upload PDF via /api/v4/file-urls/batch, poll batch results, unzip.

    Returns (full.md text, directory containing full.md for relative image paths, temp root to delete).
    Caller must shutil.rmtree(temp_cleanup_root) when finished.
    """
    path = Path(pdf_path).resolve()
    if not path.is_file():
        raise MinerUExtractError(f"PDF not found: {pdf_path}")

    base = base_url.rstrip("/")
    batch_url = f"{base}/api/v4/file-urls/batch"
    headers = {
        "Content-Type": "application/json",
        "Accept": "*/*",
        "Authorization": f"Bearer {api_token}",
    }
    payload = {
        "files": [{"name": path.name}],
        "model_version": model_version,
        "language": language,
    }

    pdf_bytes = path.read_bytes()
    tmp_root: Optional[Path] = None

    try:
        client_timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=300)
        timeout_at = asyncio.get_running_loop().time() + timeout_seconds
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            status, res = await _post_json(session, batch_url, headers, payload)
            if status != 200 or res.get("code") != 0:
                pipeline_log(
                    f"MinerU batch request failed: status={status} body={res}",
                    stage="analyze",
                    component="mineru",
                    level=logging.WARNING,
                )
                return None

            data = res.get("data") or {}
            batch_id = data.get("batch_id")
            file_urls = data.get("file_urls") or []
            if not batch_id or not file_urls:
                pipeline_log(
                    f"MinerU missing batch_id or file_urls: {res}",
                    stage="analyze",
                    component="mineru",
                    level=logging.WARNING,
                )
                return None

            put_status = await _put_file(session, file_urls[0], pdf_bytes)
            if put_status not in (200, 201, 204):
                pipeline_log(
                    f"MinerU upload failed HTTP {put_status}",
                    stage="analyze",
                    component="mineru",
                    level=logging.WARNING,
                )
                return None

            poll_url = f"{base}/api/v4/extract-results/batch/{batch_id}"
            batch_payload: Dict[str, Any] = {}
            last_states: List[str] = []

            while asyncio.get_running_loop().time() < timeout_at:
                _, poll_res = await _get_json(session, poll_url, headers)
                if poll_res.get("code") != 0:
                    await asyncio.sleep(poll_interval_seconds)
                    continue

                batch_payload = poll_res.get("data") or {}
                results = batch_payload.get("extract_result") or []
                if isinstance(results, dict):
                    results = [results]
                last_states = [
                    (item.get("state") or "").lower() for item in results if isinstance(item, dict)
                ]
                if _all_results_done(results):
                    break
                await asyncio.sleep(poll_interval_seconds)
            else:
                # Distinguish "still queued/processing" from "no response" so daily-quota
                # backlogs are recognisable in logs and not lumped in with hard failures.
                queue_states = {"pending", "running", "waiting", "queueing", "processing"}
                in_queue = any(s in queue_states for s in last_states) if last_states else False
                state_summary = ",".join(last_states) if last_states else "no_state_observed"
                pipeline_log(
                    f"MinerU poll timeout batch_id={batch_id} "
                    f"last_state=[{state_summary}] queued={in_queue}",
                    stage="analyze",
                    component="mineru",
                    level=logging.WARNING,
                )
                return None

            results = batch_payload.get("extract_result") or []
            if isinstance(results, dict):
                results = [results]
            if not results:
                pipeline_log(
                    "MinerU no extract_result",
                    stage="analyze",
                    component="mineru",
                    level=logging.WARNING,
                )
                return None

            item = results[0]
            if (item.get("state") or "").lower() == "failed":
                pipeline_log(
                    f"MinerU extraction failed: {item.get('err_msg') or item}",
                    stage="analyze",
                    component="mineru",
                    level=logging.WARNING,
                )
                return None

            zip_url = item.get("full_zip_url")
            if not zip_url:
                pipeline_log(
                    f"MinerU done but no full_zip_url: {item}",
                    stage="analyze",
                    component="mineru",
                    level=logging.WARNING,
                )
                return None

            zip_bytes = await _download_zip(session, zip_url)

        tmp_root = Path(tempfile.mkdtemp(prefix="mineru_"))
        unzip_dir = tmp_root / "unzip"
        unzip_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            zf.extractall(unzip_dir)

        full_md = _find_full_md(unzip_dir)
        if not full_md:
            pipeline_log(
                f"MinerU zip has no full.md under {unzip_dir}",
                stage="analyze",
                component="mineru",
                level=logging.WARNING,
            )
            shutil.rmtree(tmp_root, ignore_errors=True)
            return None

        text = full_md.read_text(encoding="utf-8", errors="replace")
        md_dir = full_md.parent
        return text, md_dir, tmp_root

    except asyncio.CancelledError:
        raise
    except Exception as e:
        pipeline_log(
            f"MinerU extraction error: {e}",
            stage="analyze",
            component="mineru",
            level=logging.WARNING,
        )
        logger.exception("MinerU extraction error")
        if tmp_root is not None:
            shutil.rmtree(tmp_root, ignore_errors=True)
        return None

"""Browser runtime configuration helpers for website downloads."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from utils.pipeline_log import pipeline_log

logger = logging.getLogger(__name__)

_DEFAULT_AGENT_TIMEOUT_SECONDS = 300
_DEFAULT_AGENT_TIMEOUT_GRACE_SECONDS = 5
_DEFAULT_AGENT_INTERNAL_TIMEOUT_RETRIES = 1
_DEFAULT_STARTUP_RETRY_BACKOFF_SECONDS = 3.0
_DEFAULT_NETWORK_IDLE_PAGE_LOAD_SECONDS = 1.0
_DEFAULT_DEBUG_FALLBACK_CAPTURE_SECONDS = 3.0
_DEFAULT_FAST_SOURCE_TIMEOUT_SECONDS = 120
_DEFAULT_PUBLISHER_TIMEOUT_SECONDS = 180
_DEFAULT_USE_CDP = False
_DEFAULT_CHROMIUM_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-dbus",
    "--disable-background-networking",
]

def parse_agent_timeout_seconds() -> int:
    raw = os.getenv("DOWNLOAD_AGENT_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return _DEFAULT_AGENT_TIMEOUT_SECONDS
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "Invalid DOWNLOAD_AGENT_TIMEOUT_SECONDS=%r; using default=%s",
            raw,
            _DEFAULT_AGENT_TIMEOUT_SECONDS,
        )
        return _DEFAULT_AGENT_TIMEOUT_SECONDS
    if value <= 0:
        return _DEFAULT_AGENT_TIMEOUT_SECONDS
    return value


def _parse_positive_int_env(var_name: str, default: int) -> int:
    raw = os.getenv(var_name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid %s=%r; using default=%s", var_name, raw, default)
        return default
    if value <= 0:
        return default
    return value


def resolve_source_specific_timeout_seconds(
    *,
    default_timeout_seconds: int,
    repository: Optional[str],
    urls: list[str],
) -> int:
    """Use shorter browser limits for sources that should be handled directly or quickly."""
    haystack = " ".join([repository or ""] + list(urls or [])).lower()
    fast_source_tokens = (
        "ncbi.nlm.nih.gov/geo",
        "geo/query",
        "github.com",
        "raw.githubusercontent.com",
        "figshare.com",
        "zenodo.org",
        "osf.io",
        "gdc.cancer.gov",
        "portal.gdc.cancer.gov",
    )
    publisher_tokens = (
        "doi.org",
        "nature.com",
        "sciencedirect.com",
        "cell.com",
        "wiley.com",
        "springer.com",
        "plos.org",
        "pmc.ncbi.nlm.nih.gov",
        "acs.org",
        "rsc.org",
    )
    if any(token in haystack for token in fast_source_tokens):
        fast_timeout = _parse_positive_int_env(
            "DOWNLOAD_AGENT_FAST_SOURCE_TIMEOUT_SECONDS",
            _DEFAULT_FAST_SOURCE_TIMEOUT_SECONDS,
        )
        return min(default_timeout_seconds, fast_timeout)
    if any(token in haystack for token in publisher_tokens):
        publisher_timeout = _parse_positive_int_env(
            "DOWNLOAD_AGENT_PUBLISHER_TIMEOUT_SECONDS",
            _DEFAULT_PUBLISHER_TIMEOUT_SECONDS,
        )
        return min(default_timeout_seconds, publisher_timeout)
    return default_timeout_seconds


def parse_agent_timeout_grace_seconds(timeout_seconds: int) -> int:
    raw = os.getenv("DOWNLOAD_AGENT_TIMEOUT_GRACE_SECONDS", "").strip()
    if not raw:
        return _DEFAULT_AGENT_TIMEOUT_GRACE_SECONDS
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "Invalid DOWNLOAD_AGENT_TIMEOUT_GRACE_SECONDS=%r; using default=%s",
            raw,
            _DEFAULT_AGENT_TIMEOUT_GRACE_SECONDS,
        )
        return _DEFAULT_AGENT_TIMEOUT_GRACE_SECONDS
    if value < 0:
        return _DEFAULT_AGENT_TIMEOUT_GRACE_SECONDS
    if value >= timeout_seconds:
        return max(0, timeout_seconds - 1)
    return value


def parse_internal_timeout_retries() -> int:
    raw = os.getenv("DOWNLOAD_AGENT_INTERNAL_TIMEOUT_RETRIES", "").strip()
    if not raw:
        return _DEFAULT_AGENT_INTERNAL_TIMEOUT_RETRIES
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "Invalid DOWNLOAD_AGENT_INTERNAL_TIMEOUT_RETRIES=%r; using default=%s",
            raw,
            _DEFAULT_AGENT_INTERNAL_TIMEOUT_RETRIES,
        )
        return _DEFAULT_AGENT_INTERNAL_TIMEOUT_RETRIES
    if value < 0:
        return _DEFAULT_AGENT_INTERNAL_TIMEOUT_RETRIES
    return value


def is_browser_start_watchdog_timeout(message: str) -> bool:
    return "BrowserStartEvent" in message and "timed out after 30.0s" in message


def parse_startup_retry_backoff_seconds() -> float:
    raw = os.getenv("DOWNLOAD_AGENT_STARTUP_RETRY_BACKOFF_SECONDS", "").strip()
    if not raw:
        return _DEFAULT_STARTUP_RETRY_BACKOFF_SECONDS
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "Invalid DOWNLOAD_AGENT_STARTUP_RETRY_BACKOFF_SECONDS=%r; using default=%s",
            raw,
            _DEFAULT_STARTUP_RETRY_BACKOFF_SECONDS,
        )
        return _DEFAULT_STARTUP_RETRY_BACKOFF_SECONDS
    if value < 0:
        return _DEFAULT_STARTUP_RETRY_BACKOFF_SECONDS
    return value


def parse_network_idle_page_load_seconds() -> float:
    raw = os.getenv("DOWNLOAD_AGENT_NETWORK_IDLE_PAGE_LOAD_SECONDS", "").strip()
    if not raw:
        return _DEFAULT_NETWORK_IDLE_PAGE_LOAD_SECONDS
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "Invalid DOWNLOAD_AGENT_NETWORK_IDLE_PAGE_LOAD_SECONDS=%r; using default=%s",
            raw,
            _DEFAULT_NETWORK_IDLE_PAGE_LOAD_SECONDS,
        )
        return _DEFAULT_NETWORK_IDLE_PAGE_LOAD_SECONDS
    if value < 0:
        return _DEFAULT_NETWORK_IDLE_PAGE_LOAD_SECONDS
    return value


def parse_debug_fallback_capture_seconds() -> float:
    raw = os.getenv("DOWNLOAD_DEBUG_FALLBACK_CAPTURE_SECONDS", "").strip()
    if not raw:
        return _DEFAULT_DEBUG_FALLBACK_CAPTURE_SECONDS
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "Invalid DOWNLOAD_DEBUG_FALLBACK_CAPTURE_SECONDS=%r; using default=%s",
            raw,
            _DEFAULT_DEBUG_FALLBACK_CAPTURE_SECONDS,
        )
        return _DEFAULT_DEBUG_FALLBACK_CAPTURE_SECONDS
    if value <= 0:
        return _DEFAULT_DEBUG_FALLBACK_CAPTURE_SECONDS
    return value


def parse_chromium_args() -> list[str]:
    raw = os.getenv("DOWNLOAD_AGENT_CHROMIUM_ARGS", "").strip()
    if not raw:
        return list(_DEFAULT_CHROMIUM_ARGS)

    args = [part.strip() for part in raw.split(",") if part.strip()]
    if not args:
        return list(_DEFAULT_CHROMIUM_ARGS)
    return args


def parse_bool_env(var_name: str, default: bool) -> bool:
    raw = os.getenv(var_name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    logger.warning("Invalid %s=%r; using default=%s", var_name, raw, default)
    return default


def parse_use_cdp() -> bool:
    return parse_bool_env("DOWNLOAD_AGENT_USE_CDP", _DEFAULT_USE_CDP)


def resolve_browser_profile_base_path() -> Optional[Path]:
    raw = os.getenv("DOWNLOAD_AGENT_PROFILE_PATH", "").strip()
    if not raw:
        return Path(__file__).parent.parent / "browser_profile"
    normalized = raw.lower()
    if normalized in {"none", "null"}:
        return None
    return Path(raw).expanduser()


def parse_cdp_url() -> Optional[str]:
    if not parse_use_cdp():
        return None
    raw = os.getenv("DOWNLOAD_AGENT_CDP_URL", "").strip()
    if not raw:
        return None
    return raw


def log_download_browser_runtime_config(
    team_name: str,
    project_id: Optional[str],
    paper_name: str,
    profile_path_resolved: Optional[str],
    agent_timeout_seconds: int,
    timeout_grace_seconds: int,
    global_timeout_threshold: int,
    internal_timeout_retries: int,
    max_attempts: int,
    startup_retry_backoff_seconds: float,
    network_idle_page_load_seconds: float,
    cross_origin_iframes: bool,
    chromium_args: list[str],
    cdp_url: Optional[str],
) -> None:
    cfg: Dict[str, Any] = {
        "paper": paper_name,
        "DOWNLOAD_AGENT_PROFILE_PATH": os.getenv("DOWNLOAD_AGENT_PROFILE_PATH", "") or "<unset>",
        "resolved_profile_path": profile_path_resolved,
        "DOWNLOAD_AGENT_TIMEOUT_SECONDS": agent_timeout_seconds,
        "DOWNLOAD_AGENT_TIMEOUT_GRACE_SECONDS": timeout_grace_seconds,
        "computed_global_timeout_threshold_s": global_timeout_threshold,
        "DOWNLOAD_AGENT_INTERNAL_TIMEOUT_RETRIES": internal_timeout_retries,
        "computed_max_attempts": max_attempts,
        "DOWNLOAD_AGENT_STARTUP_RETRY_BACKOFF_SECONDS": startup_retry_backoff_seconds,
        "DOWNLOAD_AGENT_NETWORK_IDLE_PAGE_LOAD_SECONDS": network_idle_page_load_seconds,
        "DOWNLOAD_AGENT_CROSS_ORIGIN_IFRAMES": cross_origin_iframes,
        "DOWNLOAD_AGENT_CHROMIUM_ARGS": chromium_args,
        "DOWNLOAD_AGENT_CDP_URL": cdp_url,
        "headless": True,
        "max_steps": 20,
    }
    pipeline_log(
        "browser-use runtime config: " + json.dumps(cfg, ensure_ascii=False, default=str),
        stage="download",
        team=team_name,
        project_id=project_id,
    )

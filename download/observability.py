"""Observability helpers for website download execution."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from utils.pipeline_log import pipeline_log
from utils.pipeline_messages import pm_with_policy

LOGIN_TEXT_PATTERN = re.compile(
    r"\b(sign in|log in|login|username|password|account required|institutional login)\b",
    re.IGNORECASE,
)
CAPTCHA_TEXT_PATTERN = re.compile(
    r"\b(captcha|recaptcha|verify you are human|human verification|security check)\b",
    re.IGNORECASE,
)
APPROVAL_TEXT_PATTERN = re.compile(
    r"\b(request access|apply for access|approval required|data access committee|"
    r"controlled access|restricted access|dbgap|ega)\b",
    re.IGNORECASE,
)
NO_DOWNLOAD_TEXT_PATTERN = re.compile(
    r"\b(page not found|404|not available|temporarily unavailable|access denied|forbidden)\b",
    re.IGNORECASE,
)


def classify_visible_page_state(
    *,
    final_url: str = "",
    page_title: str = "",
    visible_text: str = "",
) -> Dict[str, Any]:
    """Classify visible browser state for download failure observability."""
    haystack = "\n".join([final_url or "", page_title or "", visible_text or ""])
    return {
        "detected_login": bool(LOGIN_TEXT_PATTERN.search(haystack)),
        "detected_captcha": bool(CAPTCHA_TEXT_PATTERN.search(haystack)),
        "detected_approval_required": bool(APPROVAL_TEXT_PATTERN.search(haystack)),
        "detected_no_download_page": bool(NO_DOWNLOAD_TEXT_PATTERN.search(haystack)),
    }


class DownloadStatusReporter:
    """Centralizes download-stage status emission and DB/UI persistence."""

    def __init__(
        self,
        *,
        team_name: str,
        project_id: Optional[str],
        dao: Any,
        ui_callback: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self.team_name = team_name
        self.project_id = project_id
        self.dao = dao
        self.ui_callback = ui_callback

    def update_status(self, message: str, persist: bool = True) -> None:
        pipeline_log(message, stage="download", team=self.team_name, project_id=self.project_id)
        if persist and self.project_id and self.dao and not self.ui_callback:
            try:
                self.dao.add_message(
                    project_id=self.project_id,
                    stage_name="download",
                    team_name=self.team_name,
                    content=message,
                    message_type="info",
                )
            except Exception as e:
                pipeline_log(
                    f"download status->messages insert failed: {e}",
                    stage="download",
                    team=self.team_name,
                    project_id=self.project_id,
                    level=logging.WARNING,
                )
        if self.ui_callback:
            self.ui_callback("status", message)

    def update_status_key(self, key: str, **params: Any) -> None:
        message, persist = pm_with_policy(key, **params)
        self.update_status(message, persist=persist)

    def record_download_log(
        self,
        *,
        paper_name: str,
        event_type: str,
        message: str,
        status: str = "info",
        screenshot_paths: Optional[list[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self.project_id or not self.dao:
            return
        try:
            payload = dict(metadata or {})
            payload["paper_name"] = paper_name
            if screenshot_paths:
                payload["screenshot_paths"] = screenshot_paths
            self.dao.add_execution_log(
                project_id=self.project_id,
                stage_name="download",
                team_name=self.team_name,
                event_type=event_type,
                message=message,
                severity=status,
                payload=payload,
            )
        except Exception as e:
            pipeline_log(
                f"download log insert failed event={event_type}: {e}",
                stage="download",
                team=self.team_name,
                project_id=self.project_id,
                level=logging.WARNING,
            )


class DownloadDebugCollector:
    """Persists browser history and screenshots for troubleshooting."""

    def __init__(self, *, debug_dir: Path, team_name: str, project_id: Optional[str]) -> None:
        self.debug_dir = debug_dir
        self.team_name = team_name
        self.project_id = project_id

    @staticmethod
    def slugify(value: str) -> str:
        normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", value or "")
        return normalized.strip("._") or "unknown"

    def paper_debug_dir(self, paper_name: str) -> Path:
        safe_paper = self.slugify(paper_name)
        if self.project_id:
            safe_project = self.slugify(self.project_id)
            return self.debug_dir / safe_project / safe_paper
        return self.debug_dir / safe_paper

    def attempt_debug_dir(self, paper_name: str, attempt: int) -> Path:
        return self.paper_debug_dir(paper_name) / f"attempt_{attempt:02d}"

    def write_debug_text(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def extract_data_urls(self, obj: Any, found: set, visited: set, depth: int = 0) -> None:
        if depth > 12:
            return
        obj_id = id(obj)
        if obj_id in visited:
            return
        visited.add(obj_id)

        if isinstance(obj, str):
            if (
                obj.startswith("data:image/")
                or ";base64," in obj
                or self.looks_like_base64_image_payload(obj)
            ):
                found.add(obj)
            return

        if isinstance(obj, dict):
            for key, value in obj.items():
                if key and "screenshot" in str(key).lower() and isinstance(value, str):
                    if (
                        value.startswith("data:image/")
                        or ";base64," in value
                        or self.looks_like_base64_image_payload(value)
                    ):
                        found.add(value)
                self.extract_data_urls(value, found, visited, depth + 1)
            return

        if isinstance(obj, (list, tuple, set)):
            for item in obj:
                self.extract_data_urls(item, found, visited, depth + 1)
            return

        for attr in ("screenshot", "state", "model_output", "result", "metadata", "history"):
            if hasattr(obj, attr):
                try:
                    self.extract_data_urls(getattr(obj, attr), found, visited, depth + 1)
                except Exception:
                    continue

    @staticmethod
    def looks_like_base64_image_payload(value: str) -> bool:
        candidate = value.strip()
        if len(candidate) < 120:
            return False
        if any(ch.isspace() for ch in candidate):
            return False
        image_prefixes = ("iVBOR", "/9j/", "R0lGOD", "UklGR")
        if candidate.startswith(image_prefixes):
            return True
        base64_chars = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")
        head = candidate[:256]
        return all(ch in base64_chars for ch in head)

    def decode_data_url_png(self, data_url: str) -> Optional[tuple[bytes, str]]:
        payload = data_url.strip()
        image_ext = "png"

        if payload.startswith("data:image/"):
            mime_match = re.match(r"^data:image/([a-zA-Z0-9+.-]+);base64,", payload)
            if not mime_match:
                return None
            image_ext = mime_match.group(1).lower().replace("jpeg", "jpg")
            _, payload = payload.split(";base64,", 1)
        elif ";base64," in payload:
            _, payload = payload.split(";base64,", 1)
        elif payload.startswith("http://") or payload.startswith("https://"):
            return None

        try:
            raw_bytes = base64.b64decode(payload, validate=True)
        except Exception:
            return None

        if not raw_bytes:
            return None

        if raw_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            image_ext = "png"
        elif raw_bytes.startswith(b"\xff\xd8\xff"):
            image_ext = "jpg"
        elif raw_bytes.startswith(b"GIF87a") or raw_bytes.startswith(b"GIF89a"):
            image_ext = "gif"
        elif raw_bytes.startswith(b"RIFF") and b"WEBP" in raw_bytes[:16]:
            image_ext = "webp"
        elif image_ext not in {"png", "jpg", "gif", "webp"}:
            image_ext = "png"

        return raw_bytes, image_ext

    def history_to_jsonable(self, history: Any) -> Dict[str, Any]:
        for method_name in ("model_dump", "dict"):
            method = getattr(history, method_name, None)
            if callable(method):
                try:
                    payload = method()
                    if isinstance(payload, dict):
                        return payload
                except Exception:
                    continue
        return {
            "history_repr": repr(history),
            "final_result": getattr(history, "final_result", lambda: None)(),
        }

    @staticmethod
    def screenshot_digest(image_bytes: bytes) -> str:
        return hashlib.sha256(image_bytes).hexdigest()

    @staticmethod
    def format_agent_error(error: Exception) -> str:
        message = str(error)
        if isinstance(error, json.JSONDecodeError) and "Expecting value" in message:
            return (
                "CDP startup returned non-JSON response (often caused by localhost proxy interception). "
                "Ensure localhost bypasses proxy, e.g. NO_PROXY=localhost,127.0.0.1. "
                f"Original error: {message}"
            )
        if "BrowserStartEvent" in message and "timed out after 30.0s" in message:
            return (
                "Local agent error: browser startup watchdog timed out at 30s "
                "(BrowserStartEvent). This is usually a transient Playwright/browser-use startup "
                "failure. The downloader retries this class of timeout based on the configured "
                "DOWNLOAD_AGENT_INTERNAL_TIMEOUT_RETRIES value; "
                "if it still fails, check machine load and Playwright browser availability. "
                f"Original error: {message}"
            )
        return f"Local agent error: {message}"

    def persist_history_observability(
        self,
        history: Any,
        paper_name: str,
        *,
        debug_path: Optional[Path] = None,
        seen_digests: Optional[set[str]] = None,
        start_index: int = 1,
        step_events: Optional[list[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        debug_path = debug_path or self.paper_debug_dir(paper_name)
        debug_path.mkdir(parents=True, exist_ok=True)

        summary: Dict[str, Any] = {
            "debug_path": str(debug_path),
            "history_json_saved": False,
            "screenshots_saved": 0,
            "errors": [],
        }
        if step_events is not None:
            summary["step_events"] = list(step_events)

        try:
            history_payload = self.history_to_jsonable(history)
            self.write_debug_text(
                debug_path / "agent_history.json",
                json.dumps(history_payload, ensure_ascii=False, indent=2, default=str),
            )
            summary["history_json_saved"] = True
        except Exception as e:
            summary["errors"].append(f"history_json: {e}")

        data_urls: set = set()
        try:
            self.extract_data_urls(history, data_urls, set())
        except Exception as e:
            summary["errors"].append(f"extract_screenshots: {e}")

        saved = 0
        screenshot_paths: list[str] = []
        active_seen_digests = seen_digests if seen_digests is not None else set()
        next_index = start_index
        for idx, data_url in enumerate(sorted(data_urls), start=1):
            decoded = self.decode_data_url_png(data_url)
            if not decoded:
                continue
            image_bytes, image_ext = decoded
            digest = self.screenshot_digest(image_bytes)
            if digest in active_seen_digests:
                continue
            active_seen_digests.add(digest)
            png_path = debug_path / f"history_step_screenshot_{next_index:03d}.{image_ext}"
            try:
                png_path.write_bytes(image_bytes)
                saved += 1
                screenshot_paths.append(str(png_path))
                next_index += 1
            except Exception as e:
                summary["errors"].append(f"screenshot_write[{idx}]: {e}")
        summary["screenshots_saved"] = saved
        summary["screenshot_paths"] = screenshot_paths
        summary["next_index"] = next_index

        self.write_debug_text(
            debug_path / "debug_summary.json",
            json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        )
        return summary

    def persist_incremental_screenshots(
        self,
        source_obj: Any,
        debug_path: Path,
        seen_data_urls: set[str],
        seen_digests: set[str],
        next_index: int,
        file_prefix: str = "live_step_screenshot",
    ) -> tuple[int, list[str]]:
        data_urls: set[str] = set()
        self.extract_data_urls(source_obj, data_urls, set())
        new_urls = [url for url in sorted(data_urls) if url not in seen_data_urls]

        saved_paths: list[str] = []
        for data_url in new_urls:
            decoded = self.decode_data_url_png(data_url)
            seen_data_urls.add(data_url)
            if not decoded:
                continue
            image_bytes, image_ext = decoded
            digest = self.screenshot_digest(image_bytes)
            if digest in seen_digests:
                continue
            seen_digests.add(digest)
            file_path = debug_path / f"{file_prefix}_{next_index:03d}.{image_ext}"
            try:
                file_path.write_bytes(image_bytes)
                saved_paths.append(str(file_path))
                next_index += 1
            except Exception:
                continue
        return next_index, saved_paths

    async def capture_browser_screenshot_png(self, source_obj: Any) -> Optional[bytes]:
        candidates = [
            source_obj,
            getattr(source_obj, "browser", None),
            getattr(source_obj, "browser_session", None),
        ]
        browser_session = None
        for candidate in candidates:
            if candidate is None:
                continue
            if callable(getattr(candidate, "get_or_create_cdp_session", None)):
                browser_session = candidate
                break
            nested = getattr(candidate, "browser_session", None)
            if nested is not None and callable(getattr(nested, "get_or_create_cdp_session", None)):
                browser_session = nested
                break
        if browser_session is None:
            return None

        try:
            cdp_session = await browser_session.get_or_create_cdp_session()
            result = await cdp_session.cdp_client.send.Page.captureScreenshot(
                params={"format": "png"},
                session_id=cdp_session.session_id,
            )
            data = None
            if isinstance(result, dict):
                data = result.get("data")
                if data is None and isinstance(result.get("result"), dict):
                    data = result["result"].get("data")
            else:
                data = getattr(result, "data", None)
            if isinstance(data, str) and data.strip():
                return base64.b64decode(data)
        except Exception:
            pass

        try:
            page = await browser_session.must_get_current_page()
            screenshot_fn = getattr(page, "screenshot", None)
            if callable(screenshot_fn):
                png_bytes = await screenshot_fn(type="png")
                if isinstance(png_bytes, bytes) and png_bytes:
                    return png_bytes
        except Exception:
            pass

        return None

    @staticmethod
    def _resolve_page_capable_browser_session(source_obj: Any) -> Any:
        candidates = [
            source_obj,
            getattr(source_obj, "browser", None),
            getattr(source_obj, "browser_session", None),
        ]
        for candidate in candidates:
            if candidate is None:
                continue
            if callable(getattr(candidate, "must_get_current_page", None)):
                return candidate
            nested = getattr(candidate, "browser_session", None)
            if nested is not None and callable(getattr(nested, "must_get_current_page", None)):
                return nested
        return None

    async def capture_browser_page_state(self, source_obj: Any) -> Dict[str, Any]:
        """Capture final URL/title/body text from the current browser page when available."""
        browser_session = self._resolve_page_capable_browser_session(source_obj)
        if browser_session is None:
            return {}

        try:
            page = await browser_session.must_get_current_page()
        except Exception:
            return {}

        state: Dict[str, Any] = {}
        try:
            state["final_url"] = str(getattr(page, "url", "") or "")
        except Exception:
            pass

        try:
            title_fn = getattr(page, "title", None)
            if callable(title_fn):
                title = title_fn()
                if hasattr(title, "__await__"):
                    title = await title
                state["page_title"] = str(title or "")
        except Exception:
            pass

        visible_text = ""
        try:
            inner_text_fn = getattr(page, "inner_text", None)
            if callable(inner_text_fn):
                text_result = inner_text_fn("body", timeout=1000)
                if hasattr(text_result, "__await__"):
                    text_result = await text_result
                visible_text = str(text_result or "")
        except Exception:
            try:
                locator_fn = getattr(page, "locator", None)
                if callable(locator_fn):
                    body = locator_fn("body")
                    text_result = body.inner_text(timeout=1000)
                    if hasattr(text_result, "__await__"):
                        text_result = await text_result
                    visible_text = str(text_result or "")
            except Exception:
                visible_text = ""

        if visible_text:
            state["visible_text_sample"] = visible_text[:2000]

        state.update(
            classify_visible_page_state(
                final_url=state.get("final_url", ""),
                page_title=state.get("page_title", ""),
                visible_text=visible_text,
            )
        )
        return state

    async def save_browser_screenshot(
        self,
        *,
        source_obj: Any,
        debug_path: Path,
        seen_digests: set[str],
        next_index: int,
        step_index: Optional[int] = None,
        reason: str = "browser_state",
    ) -> tuple[int, Optional[str], Dict[str, Any]]:
        event: Dict[str, Any] = {
            "step_index": step_index,
            "reason": reason,
            "saved": False,
            "status": "missing",
        }
        page_state = await self.capture_browser_page_state(source_obj)
        if page_state:
            event.update(page_state)
        png_bytes = await self.capture_browser_screenshot_png(source_obj)
        if not png_bytes:
            return next_index, None, event

        digest = self.screenshot_digest(png_bytes)
        event["digest"] = digest
        if digest in seen_digests:
            event["status"] = "duplicate"
            return next_index, None, event

        seen_digests.add(digest)
        reason_slug = self.slugify(reason).lower()
        step_label = f"step_{step_index:03d}_" if step_index is not None else ""
        file_path = debug_path / f"capture_{next_index:03d}_{step_label}{reason_slug}.png"
        try:
            file_path.write_bytes(png_bytes)
        except Exception as e:
            event["status"] = "error"
            event["error"] = str(e)
            return next_index, None, event

        event["saved"] = True
        event["status"] = "saved"
        event["path"] = str(file_path)
        return next_index + 1, str(file_path), event

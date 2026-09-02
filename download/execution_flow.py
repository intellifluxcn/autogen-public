"""Execution helpers for website download agent runs."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from browser_use import Agent, Browser

from download.browser_runtime import is_browser_start_watchdog_timeout
from download.local_cdp_runtime import start_local_cdp_browser
from utils.pipeline_log import pipeline_log


@dataclass
class BrowserRuntimeSettings:
    profile_path_value: Optional[str]
    agent_timeout_seconds: int
    timeout_grace_seconds: int
    global_timeout_threshold: int
    startup_retry_backoff_seconds: float
    network_idle_page_load_seconds: float
    debug_fallback_capture_seconds: float
    chromium_args: list[str]
    cross_origin_iframes: bool
    cdp_url: Optional[str]
    max_attempts: int


def classify_browser_early_stop_reason(event: dict[str, Any], objective: str = "") -> Optional[str]:
    """Return a structured reason when a browser page is clearly non-downloadable."""
    if event.get("detected_captcha"):
        return "captcha_detected"
    if event.get("detected_approval_required"):
        return "approval_required"
    if event.get("detected_no_download_page"):
        return "no_download_page"
    if event.get("detected_login") and "Requires Authentication: Yes" not in objective:
        return "unexpected_login_page"
    return None


async def run_agent_with_retries(
    *,
    objective: str,
    paper_name: str,
    paper_downloads_dir: Path,
    runtime: BrowserRuntimeSettings,
    llm: Any,
    tools: Any,
    team_name: str,
    project_id: Optional[str],
    status_helper: Any,
    debug_helper: Any,
    prepare_browser_session: Optional[Any] = None,
    sync_downloaded_files: Optional[Any] = None,
    persist_browser_session: Optional[Any] = None,
    is_cancelled: Optional[Any] = None,
) -> Any:
    result = None

    for attempt in range(1, runtime.max_attempts + 1):
        history: Any = None
        run_succeeded = False
        live_capture_stop: Optional[asyncio.Event] = None
        live_capture_task: Optional[asyncio.Task] = None
        download_sync_task: Optional[asyncio.Task] = None
        live_capture_lock: Optional[asyncio.Lock] = None
        live_capture_paths: list[str] = []
        live_capture_seen_urls: set[str] = set()
        live_capture_seen_digests: set[str] = set()
        live_capture_next_index = 1
        step_capture_events: list[dict[str, Any]] = []
        current_step_index = 0
        synced_download_sources: set[str] = set()
        browser: Any = None
        local_cdp_handle: Any = None
        cdp_url = runtime.cdp_url
        if not cdp_url:
            local_cdp_handle = await start_local_cdp_browser(
                chromium_args=runtime.chromium_args,
                profile_path_value=runtime.profile_path_value,
                team_name=team_name,
                project_id=project_id,
                paper_name=paper_name,
            )
            cdp_url = local_cdp_handle.cdp_url
        browser = Browser(cdp_url=cdp_url)
        status_helper.update_status(
            "Browser connected via CDP."
            if runtime.cdp_url
            else (
                "Browser connected via on-demand local CDP runtime."
                if runtime.profile_path_value
                else "Browser connected via on-demand local CDP runtime (temporary profile)."
            ),
            persist=False,
        )
        agent = Agent(
            task=objective,
            browser=browser,
            llm=llm,
            tools=tools,
            max_steps=20,
        )
        agent_started_at = time.monotonic()
        try:
            if callable(prepare_browser_session):
                await prepare_browser_session(agent)
            live_debug_path = debug_helper.attempt_debug_dir(paper_name, attempt)
            live_debug_path.mkdir(parents=True, exist_ok=True)
            live_capture_stop = asyncio.Event()
            live_capture_lock = asyncio.Lock()

            async def _capture_step_screenshot(
                reason: str,
                step_index: Optional[int],
            ) -> dict[str, Any]:
                nonlocal live_capture_next_index
                if live_capture_lock is None:
                    return {}
                async with live_capture_lock:
                    live_capture_next_index, saved_path, event = await debug_helper.save_browser_screenshot(
                        source_obj=agent,
                        debug_path=live_debug_path,
                        seen_digests=live_capture_seen_digests,
                        next_index=live_capture_next_index,
                        step_index=step_index,
                        reason=reason,
                    )
                event["attempt"] = attempt
                if saved_path:
                    live_capture_paths.append(saved_path)
                step_capture_events.append(event)
                return event

            async def _on_step_start(_: Any) -> None:
                nonlocal current_step_index
                current_step_index += 1
                await _capture_step_screenshot("before_step", current_step_index)

            async def _on_step_end(_: Any) -> None:
                event = await _capture_step_screenshot("after_step", current_step_index)
                if callable(is_cancelled) and is_cancelled():
                    pipeline_log(
                        f"cancellation detected at step {current_step_index}; aborting agent",
                        stage="download",
                        team=team_name,
                        project_id=project_id,
                    )
                    raise asyncio.CancelledError("user_cancelled")
                early_stop_reason = classify_browser_early_stop_reason(event, objective)
                if early_stop_reason:
                    status_helper.record_download_log(
                        paper_name=paper_name,
                        event_type="browser_early_stop",
                        status="warning",
                        message=f"Stopped browser run early: {early_stop_reason}",
                        metadata={
                            "attempt": attempt,
                            "reason": early_stop_reason,
                            "step_index": current_step_index,
                            "final_url": event.get("final_url"),
                            "page_title": event.get("page_title"),
                            "detected_login": event.get("detected_login"),
                            "detected_captcha": event.get("detected_captcha"),
                            "detected_approval_required": event.get("detected_approval_required"),
                            "detected_no_download_page": event.get("detected_no_download_page"),
                        },
                    )
                    raise RuntimeError(f"browser_early_stop:{early_stop_reason}")

            async def _live_capture_worker() -> None:
                nonlocal live_capture_next_index
                next_fallback_capture_at = 0.0
                while not live_capture_stop.is_set():
                    try:
                        if live_capture_lock is None:
                            await asyncio.sleep(1.0)
                            continue
                        async with live_capture_lock:
                            live_capture_next_index, new_paths = (
                                debug_helper.persist_incremental_screenshots(
                                    source_obj=agent,
                                    debug_path=live_debug_path,
                                    seen_data_urls=live_capture_seen_urls,
                                    seen_digests=live_capture_seen_digests,
                                    next_index=live_capture_next_index,
                                )
                            )
                        if new_paths:
                            live_capture_paths.extend(new_paths)
                        now = time.monotonic()
                        if now >= next_fallback_capture_at:
                            await _capture_step_screenshot("polling_fallback", current_step_index or None)
                            next_fallback_capture_at = (
                                now + runtime.debug_fallback_capture_seconds
                            )
                    except Exception as capture_err:
                        pipeline_log(
                            f"live capture step failed: {capture_err}",
                            stage="download",
                            component="live_capture",
                            project_id=project_id,
                            level=logging.DEBUG,
                        )
                    await asyncio.sleep(1.0)

            async def _download_sync_worker() -> None:
                while not live_capture_stop.is_set():
                    try:
                        if callable(sync_downloaded_files):
                            await sync_downloaded_files(
                                agent,
                                attempt=attempt,
                                run_succeeded=run_succeeded,
                                synced_sources=synced_download_sources,
                            )
                    except Exception:
                        pass
                    await asyncio.sleep(2.0)

            live_capture_task = asyncio.create_task(_live_capture_worker())
            download_sync_task = asyncio.create_task(_download_sync_worker())
            status_helper.update_status(
                f"Agent execution started (attempt {attempt}/{runtime.max_attempts}, "
                f"max_steps=20, timeout={runtime.agent_timeout_seconds}s)",
                persist=False,
            )
            status_helper.record_download_log(
                paper_name=paper_name,
                event_type="agent_started",
                status="info",
                message="Browser-use agent started",
                metadata={
                    "attempt": attempt,
                    "max_attempts": runtime.max_attempts,
                    "max_steps": 20,
                    "timeout_seconds": runtime.agent_timeout_seconds,
                    "timeout_grace_seconds": runtime.timeout_grace_seconds,
                    "headless": True,
                    "network_idle_page_load_seconds": runtime.network_idle_page_load_seconds,
                    "debug_fallback_capture_seconds": runtime.debug_fallback_capture_seconds,
                    "cross_origin_iframes": runtime.cross_origin_iframes,
                    "chromium_args_count": len(runtime.chromium_args),
                    "startup_retry_backoff_seconds": runtime.startup_retry_backoff_seconds,
                    "cdp_url": runtime.cdp_url,
                },
            )
            history = await asyncio.wait_for(
                agent.run(
                    on_step_start=_on_step_start,
                    on_step_end=_on_step_end,
                ),
                timeout=runtime.agent_timeout_seconds,
            )
            result = history.final_result()
            run_succeeded = True

            status_helper.update_status("Task sequence completed.", persist=False)
            pipeline_log(
                f"Agent final_result: {result}",
                stage="download",
                team=team_name,
                project_id=project_id,
            )
            status_helper.record_download_log(
                paper_name=paper_name,
                event_type="agent_completed",
                status="info",
                message="Browser-use agent completed",
                metadata={"attempt": attempt, "final_result": result},
            )
            break
        except asyncio.TimeoutError as timeout_err:
            elapsed_seconds = time.monotonic() - agent_started_at
            reached_global_timeout = elapsed_seconds >= runtime.global_timeout_threshold

            if reached_global_timeout:
                pipeline_log(
                    f"branch=timeout strategy=stop_agent_after_{runtime.agent_timeout_seconds}s "
                    f"(elapsed={elapsed_seconds:.1f}s, partial downloads may exist on disk)",
                    stage="download",
                    team=team_name,
                    project_id=project_id,
                    level=logging.WARNING,
                )
                status_helper.update_status_key("download.timeout")
                timeout_note = live_debug_path / "timeout_note.txt"
                debug_helper.write_debug_text(
                    timeout_note,
                    f"Agent timed out near the {runtime.agent_timeout_seconds}-second global limit "
                    f"(elapsed={elapsed_seconds:.1f}s). "
                    "Check pipeline logs and downloaded files for partial progress.\n",
                )
                status_helper.record_download_log(
                    paper_name=paper_name,
                    event_type="agent_timeout",
                    status="warning",
                    message=(
                        f"Browser-use agent hit global timeout near "
                        f"{runtime.agent_timeout_seconds}s "
                        f"(elapsed={elapsed_seconds:.1f}s)"
                    ),
                    metadata={
                        "attempt": attempt,
                        "timeout_seconds": runtime.agent_timeout_seconds,
                        "timeout_grace_seconds": runtime.timeout_grace_seconds,
                        "global_timeout_threshold_seconds": runtime.global_timeout_threshold,
                        "elapsed_seconds": round(elapsed_seconds, 3),
                        "timeout_note": str(timeout_note),
                    },
                )
                break

            timeout_message = str(timeout_err)
            can_retry_startup_timeout = (
                is_browser_start_watchdog_timeout(timeout_message) and attempt < runtime.max_attempts
            )
            if can_retry_startup_timeout:
                pipeline_log(
                    "internal startup timeout detected; retrying "
                    f"(attempt={attempt}/{runtime.max_attempts}, elapsed={elapsed_seconds:.1f}s, "
                    f"backoff={runtime.startup_retry_backoff_seconds}s)",
                    stage="download",
                    team=team_name,
                    project_id=project_id,
                    level=logging.WARNING,
                )
                status_helper.update_status(
                    "Browser startup timed out (internal 30s watchdog). "
                    f"Retrying attempt {attempt + 1}/{runtime.max_attempts}...",
                    persist=False,
                )
                status_helper.record_download_log(
                    paper_name=paper_name,
                    event_type="agent_internal_timeout_retry",
                    status="warning",
                    message="Browser startup watchdog timeout; retrying agent run",
                    metadata={
                        "attempt": attempt,
                        "next_attempt": attempt + 1,
                        "max_attempts": runtime.max_attempts,
                        "elapsed_seconds": round(elapsed_seconds, 3),
                        "error": timeout_message,
                        "backoff_seconds": runtime.startup_retry_backoff_seconds,
                    },
                )
                if runtime.startup_retry_backoff_seconds > 0:
                    await asyncio.sleep(runtime.startup_retry_backoff_seconds)
                continue

            raise RuntimeError(
                "Browser-use agent raised an internal timeout before the "
                f"{runtime.agent_timeout_seconds}-second global limit "
                f"(attempt {attempt}/{runtime.max_attempts}, elapsed={elapsed_seconds:.1f}s): "
                f"{timeout_err}"
            ) from timeout_err
        finally:
            if live_capture_stop is not None:
                live_capture_stop.set()
            if live_capture_task is not None:
                try:
                    await live_capture_task
                except Exception:
                    pass
            if download_sync_task is not None:
                try:
                    await download_sync_task
                except Exception:
                    pass
            try:
                if history is None:
                    history = getattr(agent, "history", None)
                if history is not None:
                    observability = debug_helper.persist_history_observability(
                        history,
                        paper_name,
                        debug_path=live_debug_path,
                        seen_digests=live_capture_seen_digests,
                        start_index=live_capture_next_index,
                        step_events=step_capture_events,
                    )
                    merged_paths = list(
                        dict.fromkeys(live_capture_paths + observability.get("screenshot_paths", []))
                    )
                    observability["screenshot_paths"] = merged_paths
                    observability["screenshots_saved"] = len(merged_paths)
                    debug_helper.write_debug_text(
                        Path(observability["debug_path"]) / "debug_summary.json",
                        json.dumps(observability, ensure_ascii=False, indent=2, default=str),
                    )
                    pipeline_log(
                        "debug artifacts saved "
                        f"path={observability.get('debug_path')} "
                        f"history_json={observability.get('history_json_saved')} "
                        f"screenshots={observability.get('screenshots_saved')} "
                        f"errors={len(observability.get('errors', []))}",
                        stage="download",
                        team=team_name,
                        project_id=project_id,
                    )
                    status_helper.update_status(
                        "Saved download debug artifacts "
                        f"(screenshots={observability.get('screenshots_saved', 0)})"
                    )
                    status_helper.record_download_log(
                        paper_name=paper_name,
                        event_type="debug_artifacts_saved",
                        status="info",
                        message="Persisted history and screenshots",
                        screenshot_paths=observability.get("screenshot_paths", []),
                        metadata=observability,
                    )
                elif live_capture_paths or step_capture_events:
                    observability = {
                        "debug_path": str(live_debug_path),
                        "history_json_saved": False,
                        "screenshots_saved": len(live_capture_paths),
                        "screenshot_paths": list(dict.fromkeys(live_capture_paths)),
                        "step_events": list(step_capture_events),
                        "errors": [],
                    }
                    debug_helper.write_debug_text(
                        live_debug_path / "debug_summary.json",
                        json.dumps(observability, ensure_ascii=False, indent=2, default=str),
                    )
                    status_helper.record_download_log(
                        paper_name=paper_name,
                        event_type="debug_artifacts_saved",
                        status="info",
                        message="Persisted live screenshots without history payload",
                        screenshot_paths=observability["screenshot_paths"],
                        metadata=observability,
                    )
            except Exception:
                pass
            if callable(sync_downloaded_files):
                try:
                    await sync_downloaded_files(
                        agent,
                        attempt=attempt,
                        run_succeeded=run_succeeded,
                        synced_sources=synced_download_sources,
                    )
                except Exception:
                    pass
            if callable(persist_browser_session):
                try:
                    await persist_browser_session(agent, run_succeeded=run_succeeded)
                except Exception:
                    pass
            if browser is not None and not runtime.cdp_url:
                close_method = getattr(browser, "close", None)
                if callable(close_method):
                    try:
                        close_result = close_method()
                        if asyncio.iscoroutine(close_result):
                            await close_result
                    except Exception:
                        pass
            if local_cdp_handle is not None:
                try:
                    await local_cdp_handle.stop()
                except Exception:
                    pass

    return result

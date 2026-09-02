"""
Smart orchestrator UI that works with Web UI or console fallback.

This module detects whether the web backend has an active UI instance and routes
calls appropriately. Teams can import from this module without knowing which UI
system is being used.
"""

import asyncio
import logging
import sys
import os
from typing import Optional

from utils.pipeline_log import pipeline_log

logger = logging.getLogger(__name__)

# Try to detect which UI system to use
_web_ui_available = False

# Check for web UI availability
try:
    web_backend_path = os.path.join(os.path.dirname(__file__), 'web_ui', 'backend')
    if os.path.exists(web_backend_path):
        sys.path.insert(0, web_backend_path)
        from orchestrator_integration import get_ui as get_web_ui
        _web_ui_available = True
        pipeline_log(
            "Web UI backend detected and available",
            stage="pipeline",
            component="orchestrator_ui",
        )
except ImportError as e:
    logger.debug(f"Web UI not available: {e}")


def _get_web_ui_instance():
    """Active WebOrchestratorUI from the FastAPI pipeline, if any."""
    try:
        from orchestrator_integration import get_ui as _gw
        return _gw()
    except ImportError:
        return None


# Lazily captured reference to the FastAPI main event loop. Populated the
# first time _schedule_on_web_ui sees a running loop on the calling thread.
# Worker threads (e.g. asyncio.to_thread targets) then hand coroutines back
# to that loop via run_coroutine_threadsafe — otherwise sync team code
# called from a thread silently drops its UI updates.
_main_loop: Optional[asyncio.AbstractEventLoop] = None


def _schedule_on_web_ui(coro):
    """Run an async WebOrchestratorUI method from sync team code.

    Two contexts:

    * Called from the main event-loop thread (normal team code awaited by
      the pipeline): schedule on the running loop via create_task.
    * Called from a worker thread (sync code dispatched via to_thread, e.g.
      find/team.py's download_paper after the perf fix): use
      run_coroutine_threadsafe to bounce the coroutine back to the main
      loop. Without this, get_event_loop() raises in worker threads and
      every push_message inside download_paper is silently dropped — UI
      progress stops updating during downloads.
    """
    global _main_loop
    try:
        running = asyncio.get_running_loop()
        # Cache the loop the first time we see one — that's the FastAPI
        # main loop, the only one with the Socket.IO server attached.
        if _main_loop is None:
            _main_loop = running
        running.create_task(coro)
        return
    except RuntimeError:
        # No running loop in this thread; we're inside to_thread or
        # similar. Fall through to the threadsafe path.
        pass

    if _main_loop is not None and not _main_loop.is_closed():
        try:
            asyncio.run_coroutine_threadsafe(coro, _main_loop)
            return
        except Exception as e:
            logger.debug("Web UI threadsafe schedule failed: %s", e)

    # Last-resort fallback (CLI / pre-startup): run the coroutine inline.
    try:
        asyncio.run(coro)
    except Exception as e:
        logger.debug("Web UI inline run failed: %s", e)


class OrchestratorUI:
    """
    Smart UI wrapper that delegates to the appropriate UI system.

    Priority order:
    1. Web UI (if backend is running and web UI is detected)
    2. Console fallback
    """

    def __init__(self):
        self._ui_mode = self._detect_ui_mode()

        pipeline_log(
            f"OrchestratorUI initialized in {self._ui_mode} mode",
            stage="pipeline",
            component="orchestrator_ui",
        )

    def _detect_ui_mode(self) -> str:
        """Detect which UI mode to use."""
        if _web_ui_available:
            web_ui = get_web_ui()
            if web_ui:
                return "web"

        return "console"

    def start(self):
        """Start the UI."""
        if self._ui_mode == "web":
            pipeline_log(
                "Using existing web UI instance",
                stage="pipeline",
                component="orchestrator_ui",
            )
        else:
            print("=== AutoGen Research Pipeline Started (Console Mode) ===")

    def stop(self):
        """Stop the UI."""
        if self._ui_mode == "console":
            print("=== AutoGen Research Pipeline Stopped ===")

    def update_stage(self, stage_name: str):
        """Update the current stage indicator."""
        if self._ui_mode == "web":
            web_ui = get_web_ui()
            if web_ui:
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        asyncio.create_task(web_ui.update_stage(stage_name))
                    else:
                        loop.run_until_complete(web_ui.update_stage(stage_name))
                except Exception as e:
                    pipeline_log(
                        f"Failed to update stage in web UI: {e}",
                        stage="pipeline",
                        component="orchestrator_ui",
                        level=logging.WARNING,
                    )
        else:
            print(f"[STAGE] {stage_name}")

    def update_progress(self, progress: float):
        """Update the progress bar (0.0 to 1.0)."""
        if self._ui_mode == "web":
            web_ui = get_web_ui()
            if web_ui:
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        asyncio.create_task(web_ui.update_progress(progress))
                    else:
                        loop.run_until_complete(web_ui.update_progress(progress))
                except Exception as e:
                    pipeline_log(
                        f"Failed to update progress in web UI: {e}",
                        stage="pipeline",
                        component="orchestrator_ui",
                        level=logging.WARNING,
                    )
        else:
            percentage = int(progress * 100)
            print(f"[PROGRESS] {percentage}%")

    def update_status(self, status: str, status_type: str = "system"):
        """Update the status text."""
        if self._ui_mode == "web":
            web_ui = get_web_ui()
            if web_ui:
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        asyncio.create_task(web_ui.update_status(status, status_type))
                    else:
                        loop.run_until_complete(web_ui.update_status(status, status_type))
                except Exception as e:
                    pipeline_log(
                        f"Failed to update status in web UI: {e}",
                        stage="pipeline",
                        component="orchestrator_ui",
                        level=logging.WARNING,
                    )
        else:
            prefix = {
                "system": "[INFO]",
                "error": "[ERROR]",
                "warning": "[WARNING]",
                "info": "[INFO]"
            }.get(status_type, "[INFO]")
            print(f"{prefix} {status}")

    def push_message(
        self,
        message: str,
        team_name: str = None,
        message_type: str = "system",
        persist: bool = True,
    ):
        """Add a message to the chat display."""
        if self._ui_mode == "web":
            web_ui = get_web_ui()
            if web_ui:
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        asyncio.create_task(web_ui.push_message(message, team_name, message_type, persist))
                    else:
                        loop.run_until_complete(web_ui.push_message(message, team_name, message_type, persist))
                except Exception as e:
                    pipeline_log(
                        f"Failed to push message in web UI: {e}",
                        stage="pipeline",
                        component="orchestrator_ui",
                        level=logging.WARNING,
                    )
        else:
            formatted_message = f"[{team_name}] {message}" if team_name else message
            prefix = {
                "system": "[SYSTEM]",
                "user": "[USER]",
                "error": "[ERROR]",
                "stage": "[STAGE]"
            }.get(message_type, "[SYSTEM]")
            print(f"{prefix} {formatted_message}")

    def push_team_message(
        self,
        team_name: str,
        message: str,
        message_type: str = "system",
        persist: bool = True,
    ):
        """Push a message from a specific team."""
        self.push_message(message, team_name=team_name, message_type=message_type, persist=persist)

    def update_thinking(self, thinking: str, agent_name: str = None):
        """Update the thinking display."""
        if self._ui_mode == "web":
            web_ui = get_web_ui()
            if web_ui:
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        asyncio.create_task(web_ui.update_thinking(thinking, agent_name))
                    else:
                        loop.run_until_complete(web_ui.update_thinking(thinking, agent_name))
                except Exception as e:
                    pipeline_log(
                        f"Failed to update thinking in web UI: {e}",
                        stage="pipeline",
                        component="orchestrator_ui",
                        level=logging.WARNING,
                    )
        else:
            prefix = f"[{agent_name}] " if agent_name else ""
            print(f"[THINKING] {prefix}{thinking}")

    async def request_input(self, prompt: str = "Please provide input:") -> str:
        """Request input from the user and wait for response."""
        if self._ui_mode == "web":
            web_ui = get_web_ui()
            if web_ui:
                try:
                    return await web_ui.request_input(prompt)
                except Exception as e:
                    pipeline_log(
                        f"Web UI input failed, falling back to console: {e}",
                        stage="pipeline",
                        component="orchestrator_ui",
                        level=logging.WARNING,
                    )

        print(f"\n[INPUT REQUIRED] {prompt}")
        try:
            response = await asyncio.to_thread(input, "Your response: ")
            print(f"[USER INPUT] {response}")
            return response.strip()
        except EOFError:
            pipeline_log(
                "EOF received, returning empty response",
                stage="pipeline",
                component="orchestrator_ui",
                level=logging.WARNING,
            )
            return ""

# Global UI instance for teams to use
_global_ui = None
_orchestrator_instance = None

def set_orchestrator(orchestrator):
    """Set the global orchestrator instance for database logging."""
    global _orchestrator_instance
    _orchestrator_instance = orchestrator

def get_ui() -> Optional[OrchestratorUI]:
    """Get the global UI instance."""
    return _global_ui

def set_ui(ui: OrchestratorUI):
    """Set the global UI instance."""
    global _global_ui
    _global_ui = ui

# Convenience functions that teams can import directly
def push_message(
    message: str,
    team_name: str = "",
    message_type: str = "info",
    persist: bool = True,
):
    """Push a message to the global UI if available."""
    _snip = message if len(message) <= 600 else message[:600] + "…"
    pipeline_log(
        f"[push] team={team_name or '-'} type={message_type} {_snip}",
        stage="pipeline",
        component="orchestrator_ui",
        level=logging.DEBUG,
    )

    ui = get_ui()
    if ui:
        ui.push_team_message(team_name, message, message_type, persist=persist)
    else:
        web_ui = _get_web_ui_instance()
        if web_ui:
            _schedule_on_web_ui(web_ui.push_message(message, team_name, message_type, persist))

    if persist and hasattr(_orchestrator_instance, 'dao') and hasattr(_orchestrator_instance, 'current_project_id'):
        if _orchestrator_instance.current_project_id:
            _orchestrator_instance.dao.add_message(
                _orchestrator_instance.current_project_id,
                message,
                team_name=team_name,
                message_type=message_type
            )


def update_stage(stage_name: str):
    """Update the stage in the global UI if available."""
    ui = get_ui()
    if ui:
        ui.update_stage(stage_name)
    else:
        web_ui = _get_web_ui_instance()
        if web_ui:
            _schedule_on_web_ui(web_ui.update_stage(stage_name))

def update_progress(progress: float):
    """Update the progress in the global UI if available."""
    ui = get_ui()
    if ui:
        ui.update_progress(progress)
    else:
        web_ui = _get_web_ui_instance()
        if web_ui:
            _schedule_on_web_ui(web_ui.update_progress(progress))

def update_status(status: str, status_type: str = "system"):
    """Update the status in the global UI if available."""
    ui = get_ui()
    if ui:
        ui.update_status(status, status_type)
    else:
        web_ui = _get_web_ui_instance()
        if web_ui:
            _schedule_on_web_ui(web_ui.update_status(status, status_type))

def update_thinking(thinking: str, agent_name: str = ""):
    """Update the thinking display in the global UI if available."""
    ui = get_ui()
    if ui:
        ui.update_thinking(thinking, agent_name)

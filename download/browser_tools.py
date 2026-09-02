"""Tool registration helpers for website download automation."""

from __future__ import annotations

import json
import logging
import sys
import asyncio
from pathlib import Path
from typing import Optional

from browser_use import ActionResult, Tools

from download.download_queue import DownloadQueue
from utils.pipeline_log import pipeline_log


def create_hitl_tools(
    *,
    user_email: Optional[str],
    team_name: str,
    project_id: Optional[str],
    download_queue: Optional[DownloadQueue] = None,
) -> Tools:
    tools = Tools()

    if download_queue:

        @tools.registry.action(
            "Queue a large file URL for background download (use for files >50MB that may timeout)"
        )
        def queue_background_download(url: str, filename: str = None) -> ActionResult:
            success = download_queue.add_download(url, filename)
            if success:
                return ActionResult(
                    extracted_content=(
                        f"Queued URL for background download: {url}. "
                        "File will download after browser session."
                    ),
                    include_in_memory=True,
                )
            return ActionResult(
                extracted_content=f"URL already queued or downloaded: {url}",
                include_in_memory=True,
            )

    @tools.registry.action("Ask human for help with authentication, paywall, or captcha")
    def request_human_help(problem_description: str) -> ActionResult:
        # Prefer Web UI async HITL channel when available.
        try:
            from orchestrator_ui import get_ui  # lazy import to avoid circular deps

            ui = get_ui()
            if ui and hasattr(ui, "request_input"):
                prompt = (
                    "AGENT NEEDS HUMAN HELP\n\n"
                    f"Problem: {problem_description}\n\n"
                    "What should I do? Please describe the action to take.\n"
                    "(Type 'skip' to skip this step.)"
                )
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        # Browser tool actions are sync; avoid deadlock in running loop.
                        user_response = ""
                    else:
                        user_response = loop.run_until_complete(ui.request_input(prompt)).strip()
                except Exception:
                    user_response = ""

                if user_response:
                    if user_response.lower() == "skip":
                        return ActionResult(
                            extracted_content="User chose to skip this step. Moving on to next task.",
                            include_in_memory=True,
                        )
                    return ActionResult(
                        extracted_content=f"Human provided guidance: {user_response}",
                        include_in_memory=True,
                    )
        except Exception:
            pass

        print("\n" + "=" * 80)
        print("AGENT NEEDS HUMAN HELP")
        print("=" * 80)
        print(f"\nProblem: {problem_description}")
        print("\nWhat should I do? Please describe the action to take:")
        print("(Or type 'skip' to skip this step and move on)")
        print("-" * 80)

        try:
            user_response = input("Your response: ").strip()
        except EOFError:
            pipeline_log(
                "request_human_help received EOF; auto-skipping step in non-interactive mode",
                stage="download",
                team=team_name,
                project_id=project_id,
                level=logging.WARNING,
            )
            return ActionResult(
                extracted_content=(
                    "No interactive stdin available (EOF). "
                    "Auto-skipped this human-help step and continued."
                ),
                include_in_memory=True,
            )

        if user_response.lower() == "skip":
            return ActionResult(
                extracted_content="User chose to skip this step. Moving on to next task.",
                include_in_memory=True,
            )

        return ActionResult(
            extracted_content=f"Human provided guidance: {user_response}",
            include_in_memory=True,
        )

    try:
        backend_dir = Path(__file__).resolve().parents[1] / "web_ui" / "backend"
        if str(backend_dir) not in sys.path:
            sys.path.insert(0, str(backend_dir))
        from linked_accounts import ensure_table, get_linked_account_secrets  # type: ignore

        ensure_table()

        @tools.registry.action("Get linked account credentials for a login site/domain")
        def get_linked_account_credentials(site: str) -> ActionResult:
            if not user_email:
                return ActionResult(
                    extracted_content="No project user_email available; cannot fetch linked accounts.",
                    include_in_memory=True,
                )
            secret = get_linked_account_secrets(user_email, site)
            if not secret:
                return ActionResult(
                    extracted_content=f"No linked account credentials found for '{site}'.",
                    include_in_memory=True,
                )
            return ActionResult(
                extracted_content=json.dumps(
                    {
                        "site": secret.get("site"),
                        "linked_account_id": secret.get("id"),
                        "credential": secret.get("credential"),
                        "password": secret.get("password"),
                    }
                ),
                include_in_memory=True,
            )
    except Exception as e:
        pipeline_log(
            f"Linked accounts tool disabled: {e}",
            stage="download",
            team=team_name,
            project_id=project_id,
            level=logging.WARNING,
        )

    return tools

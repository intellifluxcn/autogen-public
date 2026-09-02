"""
Website download executor using browser-use local automation.

This module preserves the legacy DownloadTeam entry point while exposing the
strategy-specific WebsiteDownloadTeam used by the acquisition router.
"""

import asyncio
import hashlib
import logging
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional
from dotenv import load_dotenv
from browser_use import ChatOpenAI

from download.cost_tracking_llm import CostTrackingChatOpenAI
from download.artifact_finalize import finalize_download_run
from utils.path_utils import get_browseruse_datasets_dir
from utils.llm_config import (
    get_openrouter_api_key,
    get_openrouter_base_url,
    get_openrouter_default_headers,
    get_openrouter_download_model,
)
from utils.pipeline_log import pipeline_log
from utils.pipeline_messages import pm
from download.browser_runtime import (
    log_download_browser_runtime_config,
    parse_agent_timeout_grace_seconds,
    parse_agent_timeout_seconds,
    parse_bool_env,
    parse_cdp_url,
    parse_chromium_args,
    parse_debug_fallback_capture_seconds,
    parse_internal_timeout_retries,
    parse_network_idle_page_load_seconds,
    parse_startup_retry_backoff_seconds,
    resolve_browser_profile_base_path,
    resolve_source_specific_timeout_seconds,
)
from download.browser_tools import create_hitl_tools
from download.download_queue import DownloadQueue
from download.execution_flow import BrowserRuntimeSettings, run_agent_with_retries
from download.filename_utils import normalize_download_filename, normalize_downloaded_file_path
from download.observability import DownloadDebugCollector, DownloadStatusReporter
from download.plan_adapter import (
    artifact_source_for_path,
    build_downloaded_artifact_descriptor,
    generate_prompt_from_plan,
    load_download_plan,
    resolve_repository_task,
)
from download.profile_cache import (
    DownloadProfileCache,
    candidate_sites_from_urls,
    extract_cookies_from_browser,
    restore_cookies_to_browser,
)
from analyze.plan_model import AcquisitionTask, DownloadPlan
from database.dao import ProjectDAO
from download.acquisition_result import (
    AcquisitionTaskResult,
    CoverageSummary,
    ProducedArtifact,
)
from download.artifact_validation import infer_coverage_for_artifacts, validate_downloaded_file

load_dotenv()

logger = logging.getLogger(__name__)


class WebsiteDownloadTeam:

    def __init__(self, ui_callback=None, team_name: str = "WebsiteDownloadTeam", project_id: str = None,
                 cancellation_check=None, download_model_override: Optional[str] = None):
        self.ui_callback = ui_callback
        self.team_name = team_name
        self.project_id = project_id
        self.cancellation_check = cancellation_check
        self.download_model_override = download_model_override
        self.user_email: str | None = None
        self.downloads_dir = Path(get_browseruse_datasets_dir())
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        self.debug_dir = Path(__file__).resolve().parents[1] / "outputs" / "download_debug"
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        self.dao: Optional[ProjectDAO] = ProjectDAO() if self.project_id else None
        self.status_reporter = DownloadStatusReporter(
            team_name=self.team_name,
            project_id=self.project_id,
            dao=self.dao,
            ui_callback=self.ui_callback,
        )
        self.debug_collector = DownloadDebugCollector(
            debug_dir=self.debug_dir,
            team_name=self.team_name,
            project_id=self.project_id,
        )
        pipeline_log(
            f"datasets_dir={self.downloads_dir}",
            stage="download",
            team=self.team_name,
            project_id=self.project_id,
        )
        from storage import get_storage
        self.storage = get_storage()

    def _create_hitl_tools(self, download_queue: DownloadQueue = None):
        return create_hitl_tools(
            user_email=self.user_email,
            team_name=self.team_name,
            project_id=self.project_id,
            download_queue=download_queue,
        )

    def _update_status(self, message: str, persist: bool = True):
        self.status_reporter.update_status(message, persist=persist)

    def _update_status_key(self, key: str, **params: Any) -> None:
        self.status_reporter.update_status_key(key, **params)

    @staticmethod
    def _slugify(value: str) -> str:
        return DownloadDebugCollector.slugify(value)

    def _paper_debug_dir(self, paper_name: str) -> Path:
        return self.debug_collector.paper_debug_dir(paper_name)

    def _build_isolated_profile_path(self, paper_name: str) -> Optional[Path]:
        base_path = resolve_browser_profile_base_path()
        if base_path is None:
            return None

        run_token = uuid.uuid4().hex[:8]
        parts = ["runs"]
        if self.project_id:
            parts.append(self._slugify(self.project_id))
        parts.append(self._slugify(paper_name))
        parts.append(run_token)
        return base_path.joinpath(*parts)

    @staticmethod
    def _build_profile_cache() -> Optional[DownloadProfileCache]:
        base_path = resolve_browser_profile_base_path()
        if base_path is None:
            return None
        return DownloadProfileCache(base_dir=base_path)

    @staticmethod
    def _resolve_profile_cache_site(
        plan: DownloadPlan,
        repository_task: AcquisitionTask,
    ) -> Optional[str]:
        step_urls = [step.url for step in repository_task.steps]
        candidates = candidate_sites_from_urls(step_urls)
        if candidates:
            return candidates[0]
        return DownloadProfileCache.normalize_site(plan.repository)

    def _cleanup_profile_path(self, profile_path: Optional[Path]) -> None:
        if profile_path is None:
            return
        try:
            shutil.rmtree(profile_path, ignore_errors=True)
            pipeline_log(
                f"removed run profile path={profile_path}",
                stage="download",
                team=self.team_name,
                project_id=self.project_id,
            )
        except Exception as e:
            pipeline_log(
                f"failed to remove run profile path={profile_path}: {e}",
                stage="download",
                team=self.team_name,
                project_id=self.project_id,
                level=logging.WARNING,
            )

    @staticmethod
    def _should_persist_profile_cache(run_error: Optional[Exception]) -> bool:
        return run_error is None

    async def _prepare_browser_session_with_cache(
        self,
        *,
        agent: Any,
        profile_cache: Optional[DownloadProfileCache],
        profile_cache_site: Optional[str],
    ) -> None:
        if profile_cache is None or not profile_cache_site:
            return
        cookies = profile_cache.load_cookies(
            site=profile_cache_site,
            user_email=self.user_email,
        )
        restored_count = await restore_cookies_to_browser(agent, cookies)
        pipeline_log(
            f"profile cache cookies restored site={profile_cache_site!r} count={restored_count}",
            stage="download",
            team=self.team_name,
            project_id=self.project_id,
        )

    async def _persist_browser_session_to_cache(
        self,
        *,
        agent: Any,
        profile_cache: Optional[DownloadProfileCache],
        profile_cache_site: Optional[str],
        run_succeeded: bool,
    ) -> None:
        if profile_cache is None or not profile_cache_site:
            return
        if not run_succeeded:
            pipeline_log(
                f"profile cache site={profile_cache_site!r} persisted=False reason=run_failed",
                stage="download",
                team=self.team_name,
                project_id=self.project_id,
            )
            return
        cookies = await extract_cookies_from_browser(agent)
        persisted = profile_cache.save_cookies(
            site=profile_cache_site,
            user_email=self.user_email,
            cookies=cookies,
        )
        pipeline_log(
            f"profile cache cookies persisted site={profile_cache_site!r} count={len(cookies)} persisted={persisted}",
            stage="download",
            team=self.team_name,
            project_id=self.project_id,
        )

    @staticmethod
    def _resolve_browser_session(source_obj: Any) -> Any:
        candidates = [
            source_obj,
            getattr(source_obj, "browser", None),
            getattr(source_obj, "browser_session", None),
        ]
        for candidate in candidates:
            if candidate is None:
                continue
            if getattr(candidate, "downloaded_files", None) is not None:
                return candidate
            nested = getattr(candidate, "browser_session", None)
            if nested is not None and getattr(nested, "downloaded_files", None) is not None:
                return nested
        return None

    @staticmethod
    def _resolve_browser_downloads_path(source_obj: Any) -> Optional[Path]:
        browser_session = WebsiteDownloadTeam._resolve_browser_session(source_obj)
        if browser_session is None:
            return None
        try:
            browser_profile = getattr(browser_session, "browser_profile", None)
            downloads_path = getattr(browser_profile, "downloads_path", None)
            if downloads_path:
                return Path(downloads_path)
        except Exception:
            return None
        return None

    @staticmethod
    def _file_digest(path: Path, chunk_size: int = 1024 * 1024) -> Optional[str]:
        hasher = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                while True:
                    chunk = handle.read(chunk_size)
                    if not chunk:
                        break
                    hasher.update(chunk)
        except OSError:
            return None
        return hasher.hexdigest()

    @staticmethod
    def _target_download_path(destination_dir: Path, source_path: Path) -> Path:
        duplicate_path = WebsiteDownloadTeam._existing_duplicate_download_path(
            destination_dir,
            source_path,
        )
        if duplicate_path is not None:
            return duplicate_path

        target_name = normalize_download_filename(source_path.name, source_path=source_path)
        target_path = destination_dir / target_name
        if not target_path.exists():
            return target_path

        try:
            if target_path.stat().st_size == source_path.stat().st_size:
                source_digest = WebsiteDownloadTeam._file_digest(source_path)
                target_digest = WebsiteDownloadTeam._file_digest(target_path)
                if source_digest and target_digest and source_digest == target_digest:
                    return target_path
        except OSError:
            pass

        stem = source_path.stem
        suffix = source_path.suffix
        counter = 1
        while True:
            candidate = destination_dir / f"{stem}_{counter}{suffix}"
            if not candidate.exists():
                return candidate
            counter += 1

    @staticmethod
    def _existing_duplicate_download_path(destination_dir: Path, source_path: Path) -> Optional[Path]:
        try:
            source_size_before = source_path.stat().st_size
        except OSError:
            return None
        source_digest = WebsiteDownloadTeam._file_digest(source_path)
        if not source_digest or not destination_dir.exists():
            return None
        # Verify source file wasn't modified while we were hashing it (TOCTOU guard)
        try:
            if source_path.stat().st_size != source_size_before:
                return None
        except OSError:
            return None
        try:
            existing_paths = sorted(destination_dir.iterdir())
        except OSError:
            return None
        for existing_path in existing_paths:
            if existing_path == source_path or not existing_path.is_file():
                continue
            try:
                existing_size = existing_path.stat().st_size
                if existing_size != source_size_before:
                    continue
            except OSError:
                continue
            existing_digest = WebsiteDownloadTeam._file_digest(existing_path)
            # Second stat after hashing: skip if file changed under us
            try:
                if existing_path.stat().st_size != existing_size:
                    continue
            except OSError:
                continue
            if existing_digest == source_digest:
                return existing_path
        return None

    async def _sync_browser_downloads_to_dir(
        self,
        *,
        agent: Any,
        paper_name: str,
        destination_dir: Path,
        attempt: int,
        run_succeeded: bool,
        synced_sources: Optional[set[str]] = None,
        plan: Optional[DownloadPlan] = None,
        task_override: Optional[AcquisitionTask] = None,
    ) -> list[str]:
        discovered_paths: list[str] = []
        browser_session = self._resolve_browser_session(agent)
        if browser_session is not None:
            try:
                downloaded_files = browser_session.downloaded_files
                if callable(downloaded_files):
                    downloaded_files = downloaded_files()
                if isinstance(downloaded_files, list):
                    discovered_paths.extend(str(path) for path in downloaded_files)
            except Exception:
                pass

        downloads_path = self._resolve_browser_downloads_path(agent)
        if downloads_path and downloads_path.exists():
            try:
                for path in sorted(downloads_path.iterdir()):
                    if path.is_file():
                        discovered_paths.append(str(path))
            except OSError:
                pass

        if not discovered_paths:
            return []

        synced_paths: list[str] = []
        destination_dir.mkdir(parents=True, exist_ok=True)
        active_synced_sources = synced_sources if synced_sources is not None else set()

        for raw_path in dict.fromkeys(discovered_paths):
            if raw_path in active_synced_sources:
                continue
            source_path = Path(raw_path)
            if not source_path.exists() or not source_path.is_file():
                continue
            if source_path.suffix == ".crdownload":
                continue
            try:
                if source_path.stat().st_size == 0:
                    continue
            except OSError:
                continue

            if plan is not None:
                validation = validate_downloaded_file(
                    source_path,
                    plan=plan,
                    task_override=task_override,
                )
                if not validation.is_valid:
                    active_synced_sources.add(raw_path)
                    pipeline_log(
                        f"reject browser download path={source_path} reason={validation.reason}",
                        stage="download",
                        team=self.team_name,
                        project_id=self.project_id,
                        level=logging.WARNING,
                    )
                    self._record_download_log(
                        paper_name=paper_name,
                        event_type="browser_download_rejected",
                        status="warning",
                        message=f"Rejected invalid browser download: {source_path.name}",
                        metadata={
                            "attempt": attempt,
                            "run_succeeded": run_succeeded,
                            "source_path": str(source_path),
                            "reason": validation.reason,
                            "validation": validation.metadata,
                        },
                    )
                    continue

            target_path = self._target_download_path(destination_dir, source_path)
            if target_path.exists():
                active_synced_sources.add(raw_path)
                synced_paths.append(str(target_path))
                continue

            await asyncio.to_thread(shutil.copy2, source_path, target_path)
            target_path = normalize_downloaded_file_path(target_path)
            active_synced_sources.add(raw_path)
            synced_paths.append(str(target_path))

        if synced_paths:
            pipeline_log(
                f"sync browser downloads: copied {len(synced_paths)} file(s) into {destination_dir}",
                stage="download",
                team=self.team_name,
                project_id=self.project_id,
            )
            self._record_download_log(
                paper_name=paper_name,
                event_type="browser_downloads_synced",
                status="info",
                message=(
                    f"Synchronized {len(synced_paths)} browser download(s) into project directory"
                ),
                metadata={
                    "attempt": attempt,
                    "run_succeeded": run_succeeded,
                    "destination_dir": str(destination_dir),
                    "synced_paths": synced_paths,
                },
            )

        return synced_paths

    def _write_debug_text(self, path: Path, content: str) -> None:
        self.debug_collector.write_debug_text(path, content)

    def _record_download_log(
        self,
        *,
        paper_name: str,
        event_type: str,
        message: str,
        status: str = "info",
        screenshot_paths: Optional[list[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.status_reporter.record_download_log(
            paper_name=paper_name,
            event_type=event_type,
            message=message,
            status=status,
            screenshot_paths=screenshot_paths,
            metadata=metadata,
        )

    def _extract_data_urls(self, obj: Any, found: set, visited: set, depth: int = 0) -> None:
        self.debug_collector.extract_data_urls(obj, found, visited, depth)

    @staticmethod
    def _looks_like_base64_image_payload(value: str) -> bool:
        return DownloadDebugCollector.looks_like_base64_image_payload(value)

    def _decode_data_url_png(self, data_url: str) -> Optional[tuple[bytes, str]]:
        return self.debug_collector.decode_data_url_png(data_url)

    def _history_to_jsonable(self, history: Any) -> Dict[str, Any]:
        return self.debug_collector.history_to_jsonable(history)

    @staticmethod
    def _screenshot_digest(image_bytes: bytes) -> str:
        return DownloadDebugCollector.screenshot_digest(image_bytes)

    def _format_agent_error(self, error: Exception) -> str:
        return self.debug_collector.format_agent_error(error)

    def _persist_history_observability(self, history: Any, paper_name: str) -> Dict[str, Any]:
        return self.debug_collector.persist_history_observability(history, paper_name)

    def _persist_incremental_screenshots(
        self,
        source_obj: Any,
        debug_path: Path,
        seen_data_urls: set[str],
        seen_digests: set[str],
        next_index: int,
        file_prefix: str = "live_step_screenshot",
    ) -> tuple[int, list[str]]:
        return self.debug_collector.persist_incremental_screenshots(
            source_obj,
            debug_path,
            seen_data_urls,
            seen_digests,
            next_index,
            file_prefix=file_prefix,
        )

    async def _capture_browser_screenshot_png(self, source_obj: Any) -> Optional[bytes]:
        return await self.debug_collector.capture_browser_screenshot_png(source_obj)

    async def _wait_for_downloads_to_complete(self, download_dir: Path, timeout_seconds: int = None) -> bool:
        import time
        if timeout_seconds is None:
            timeout_seconds = int(os.getenv("DOWNLOAD_WAIT_TIMEOUT_SECONDS") or "600")
        start_time = time.time()
        last_log_elapsed = -1

        while True:
            crdownload_files = list(download_dir.glob('*.crdownload'))

            if not crdownload_files:
                pipeline_log(
                    "All in-progress .crdownload cleared",
                    stage="download",
                    team=self.team_name,
                    project_id=self.project_id,
                )
                return True

            elapsed = time.time() - start_time
            if elapsed > timeout_seconds:
                pipeline_log(
                    f"Wait timeout ({timeout_seconds}s). {len(crdownload_files)} incomplete: "
                    f"{[f.name for f in crdownload_files]}",
                    stage="download",
                    team=self.team_name,
                    project_id=self.project_id,
                    level=logging.WARNING,
                )
                return False

            if int(elapsed) // 15 > last_log_elapsed // 15 and int(elapsed) > 0:
                last_log_elapsed = int(elapsed)
                pipeline_log(
                    f"waiting on {len(crdownload_files)} crdownload file(s) elapsed={int(elapsed)}s timeout={timeout_seconds}s",
                    stage="download",
                    team=self.team_name,
                    project_id=self.project_id,
                )
                self._update_status_key(
                    "download.wait_active",
                    count=len(crdownload_files),
                    seconds=int(elapsed),
                )

            await asyncio.sleep(2)

    def _load_download_plan(self, plan_path: str) -> DownloadPlan:
        return load_download_plan(plan_path)

    @staticmethod
    def _resolve_repository_task(plan: DownloadPlan, task_override: Optional[AcquisitionTask] = None) -> AcquisitionTask:
        return resolve_repository_task(plan, task_override)

    @staticmethod
    def _artifact_source_for_path(dataset_path: Path, repository_name: Optional[str]) -> str:
        return artifact_source_for_path(dataset_path, repository_name)

    def _build_downloaded_artifact_descriptor(
        self,
        *,
        dataset_path: Path,
        paper_name: str,
        plan: DownloadPlan,
        task_title: Optional[str],
        confidence: Optional[float] = 1.0,
    ) -> ProducedArtifact:
        return build_downloaded_artifact_descriptor(
            dataset_path=dataset_path,
            paper_name=paper_name,
            plan=plan,
            task_title=task_title,
            produced_by=self.__class__.__name__,
            confidence=confidence,
        )

    def _generate_prompt_from_plan(
        self,
        plan: DownloadPlan,
        paper_name: str = None,
        task_override: Optional[AcquisitionTask] = None,
    ) -> str:
        self._update_status_key("download.generate_prompt")
        return generate_prompt_from_plan(
            plan=plan,
            paper_name=paper_name,
            downloads_dir=self.downloads_dir,
            task_override=task_override,
        )

    async def run(
        self,
        plan_path: str = None,
        plan_data: Dict[str, Any] = None,
        task_override: Optional[AcquisitionTask] = None,
        loaded_plan: Optional[DownloadPlan] = None,
    ):
        self._update_status_key("download.start")
        run_error: Optional[Exception] = None
        download_llm: Optional["CostTrackingChatOpenAI"] = None
        download_started_at: Optional[float] = None
        dataset_count = 0
        produced_artifacts: list[ProducedArtifact] = []
        browser_profile_path: Optional[Path] = None
        profile_cache: Optional[DownloadProfileCache] = None
        profile_cache_site: Optional[str] = None

        if plan_path:
            pipeline_log(f"load plan: path={plan_path}", stage="download", team=self.team_name, project_id=self.project_id)
            if not os.path.exists(plan_path):
                error_msg = f"Plan file not found: {plan_path}"
                pipeline_log(
                    error_msg,
                    stage="download",
                    team=self.team_name,
                    project_id=self.project_id,
                    level=logging.ERROR,
                )
                self._update_status_key("download.error", error=error_msg)
                raise FileNotFoundError(error_msg)

            paper_name = Path(plan_path).stem.replace(".plan", "")

            if self.project_id:
                from database.dao import ProjectDAO
                dao = ProjectDAO()
                # Cache the owning user's linked_accounts for automated login.
                try:
                    proj = dao.get_project(self.project_id)
                    self.user_email = (proj or {}).get("user_email")
                except Exception as e:
                    pipeline_log(
                        f"Failed to resolve project user_email: {e}",
                        stage="download",
                        team=self.team_name,
                        project_id=self.project_id,
                        level=logging.WARNING,
                    )

                if dao.has_dataset_artifact(self.project_id, paper_name):
                    pipeline_log(
                        f"branch=skip reason=artifact_exists datasets already recorded for paper={paper_name!r}",
                        stage="download",
                        team=self.team_name,
                        project_id=self.project_id,
                    )
                    self._update_status_key("download.skip_already_downloaded", paper=paper_name)
                    return AcquisitionTaskResult(
                        task_type="repository_download",
                        status="skipped",
                        produced_artifacts=[],
                        coverage=CoverageSummary(),
                        remaining_gaps=[],
                        message="Datasets already recorded for this paper",
                        metadata={"paper": paper_name, "reason": "already_downloaded"},
                    ).model_dump()

            plan = loaded_plan or self._load_download_plan(plan_path)
            repository_task = self._resolve_repository_task(plan, task_override)
            profile_cache_site = self._resolve_profile_cache_site(plan, repository_task)
            actionable_actions = {"navigate", "download", "login"}
            if not repository_task.steps or not any(
                step.action in actionable_actions for step in repository_task.steps
            ):
                pipeline_log(
                    f"branch=skip reason=no_actionable_steps paper={paper_name!r} "
                    f"actions_allowed={sorted(actionable_actions)}",
                    stage="download",
                    team=self.team_name,
                    project_id=self.project_id,
                )
                self._update_status_key("download.skip_no_actionable_steps", paper=paper_name)
                return AcquisitionTaskResult(
                    task_type="repository_download",
                    status="skipped",
                    produced_artifacts=[],
                    coverage=CoverageSummary(),
                    remaining_gaps=list(repository_task.blocking_gaps or plan.blocking_gaps),
                    message="Repository task had no actionable browser steps",
                    metadata={"paper": paper_name, "reason": "no_actionable_steps"},
                ).model_dump()

            objective = self._generate_prompt_from_plan(plan, paper_name, task_override=repository_task)

        elif plan_data:
            if 'plan_path' in plan_data:
                plan_path = plan_data['plan_path']
                return await self.run(
                    plan_path=plan_path,
                    task_override=task_override,
                    loaded_plan=loaded_plan,
                )
            else:
                error_msg = "plan_data must contain 'plan_path' key with JSON plan file path"
                pipeline_log(
                    error_msg,
                    stage="download",
                    team=self.team_name,
                    project_id=self.project_id,
                    level=logging.ERROR,
                )
                raise ValueError(error_msg)
        else:
            error_msg = "Must provide either plan_path or plan_data"
            pipeline_log(
                error_msg,
                stage="download",
                team=self.team_name,
                project_id=self.project_id,
                level=logging.ERROR,
            )
            raise ValueError(error_msg)

        logger.debug("Browser objective (full): %s", objective)
        pipeline_log(
            f"plan loaded repository={plan.repository!r} access={plan.data_accessibility!r} "
            f"auth={plan.requires_authentication} steps={len(plan.steps)} "
            f"objective_chars={len(objective)}",
            stage="download",
            team=self.team_name,
            project_id=self.project_id,
        )
        self._update_status_key("download.objective_ready")

        paper_downloads_dir = self.downloads_dir / paper_name
        paper_downloads_dir.mkdir(parents=True, exist_ok=True)
        pipeline_log(
            f"download directory={paper_downloads_dir}",
            stage="download",
            team=self.team_name,
            project_id=self.project_id,
        )
        self._update_status_key("download.dir_created", paper=paper_name)

        download_queue = DownloadQueue()

        try:
            effective_model = self.download_model_override or get_openrouter_download_model()
            pipeline_log(
                f"download LLM openrouter model={effective_model}",
                stage="download",
                team=self.team_name,
                project_id=self.project_id,
            )
            # browser-use forces an OpenAI strict json_schema response_format whose
            # agent schema has integer fields with `minimum`; Anthropic models via
            # OpenRouter reject that ("output_config.format.schema: For 'integer'
            # type, property 'minimum' is not supported" → 400 on every step).
            # Put the schema in the system prompt and parse the text instead.
            llm = CostTrackingChatOpenAI(
                model=effective_model,
                api_key=get_openrouter_api_key(),
                base_url=get_openrouter_base_url(),
                default_headers=get_openrouter_default_headers(),
                dont_force_structured_output=True,
                add_schema_to_system_prompt=True,
            )
            download_llm = llm
            download_started_at = time.monotonic()
            self._update_status("Language model initialized.", persist=False)

            browser_profile_path = self._build_isolated_profile_path(paper_name)
            profile_cache = self._build_profile_cache()
            if browser_profile_path is not None:
                browser_profile_path.mkdir(parents=True, exist_ok=True)
                profile_path_value: Optional[str] = str(browser_profile_path)
                pipeline_log(
                    f"profile cache site={profile_cache_site!r} path={browser_profile_path}",
                    stage="download",
                    team=self.team_name,
                    project_id=self.project_id,
                )
            else:
                profile_path_value = None
            tools = self._create_hitl_tools(download_queue=download_queue)
            self._update_status(
                "HITL tools enabled - agent can ask for help with paywalls/captchas.",
                persist=False,
            )
            self._update_status(
                "Background download queue enabled - large files can be queued for later.",
                persist=False,
            )

            default_agent_timeout_seconds = parse_agent_timeout_seconds()
            agent_timeout_seconds = resolve_source_specific_timeout_seconds(
                default_timeout_seconds=default_agent_timeout_seconds,
                repository=plan.repository,
                urls=[step.url for step in repository_task.steps if step.url],
            )
            timeout_grace_seconds = parse_agent_timeout_grace_seconds(agent_timeout_seconds)
            global_timeout_threshold = max(1, agent_timeout_seconds - timeout_grace_seconds)
            internal_timeout_retries = parse_internal_timeout_retries()
            startup_retry_backoff_seconds = parse_startup_retry_backoff_seconds()
            network_idle_page_load_seconds = parse_network_idle_page_load_seconds()
            debug_fallback_capture_seconds = parse_debug_fallback_capture_seconds()
            chromium_args = parse_chromium_args()
            cross_origin_iframes = parse_bool_env("DOWNLOAD_AGENT_CROSS_ORIGIN_IFRAMES", True)
            cdp_url = parse_cdp_url()
            max_attempts = 1 + internal_timeout_retries
            runtime = BrowserRuntimeSettings(
                profile_path_value=profile_path_value,
                agent_timeout_seconds=agent_timeout_seconds,
                timeout_grace_seconds=timeout_grace_seconds,
                global_timeout_threshold=global_timeout_threshold,
                startup_retry_backoff_seconds=startup_retry_backoff_seconds,
                network_idle_page_load_seconds=network_idle_page_load_seconds,
                debug_fallback_capture_seconds=debug_fallback_capture_seconds,
                chromium_args=chromium_args,
                cross_origin_iframes=cross_origin_iframes,
                cdp_url=cdp_url,
                max_attempts=max_attempts,
            )
            log_download_browser_runtime_config(
                self.team_name,
                self.project_id,
                paper_name,
                profile_path_value,
                agent_timeout_seconds,
                timeout_grace_seconds,
                global_timeout_threshold,
                internal_timeout_retries,
                max_attempts,
                startup_retry_backoff_seconds,
                network_idle_page_load_seconds,
                cross_origin_iframes,
                chromium_args,
                cdp_url,
            )
            self._update_status(
                f"Agent created with HITL support. Running task with {agent_timeout_seconds}s timeout...",
                persist=False,
            )
            await run_agent_with_retries(
                objective=objective,
                paper_name=paper_name,
                paper_downloads_dir=paper_downloads_dir,
                runtime=runtime,
                llm=llm,
                tools=tools,
                team_name=self.team_name,
                project_id=self.project_id,
                status_helper=self.status_reporter,
                debug_helper=self.debug_collector,
                is_cancelled=self.cancellation_check,
                prepare_browser_session=lambda agent: self._prepare_browser_session_with_cache(
                    agent=agent,
                    profile_cache=profile_cache,
                    profile_cache_site=profile_cache_site,
                ),
                sync_downloaded_files=lambda agent, attempt=1, run_succeeded=False, synced_sources=None: self._sync_browser_downloads_to_dir(
                    agent=agent,
                    paper_name=paper_name,
                    destination_dir=paper_downloads_dir,
                    attempt=attempt,
                    run_succeeded=run_succeeded,
                    synced_sources=synced_sources,
                    plan=plan,
                    task_override=repository_task,
                ),
                persist_browser_session=lambda agent, run_succeeded=False: self._persist_browser_session_to_cache(
                    agent=agent,
                    profile_cache=profile_cache,
                    profile_cache_site=profile_cache_site,
                    run_succeeded=run_succeeded,
                ),
            )

        except Exception as e:
            formatted_error = self._format_agent_error(e)
            pipeline_log(
                formatted_error,
                stage="download",
                team=self.team_name,
                project_id=self.project_id,
                level=logging.ERROR,
            )
            logger.exception("Local agent error")
            self._update_status_key("download.error", error=formatted_error)
            self._record_download_log(
                paper_name=paper_name,
                event_type="agent_error",
                status="error",
                message=formatted_error,
            )
            run_error = RuntimeError(formatted_error)
        finally:
            dataset_count, produced_artifacts = await finalize_download_run(
                download_queue=download_queue,
                paper_name=paper_name,
                plan=plan,
                task_override=task_override,
                paper_downloads_dir=paper_downloads_dir,
                downloads_dir=self.downloads_dir,
                project_id=self.project_id,
                team_name=self.team_name,
                status_helper=self.status_reporter,
                wait_for_downloads_to_complete=self._wait_for_downloads_to_complete,
                storage=self.storage,
                build_downloaded_artifact_descriptor=self._build_downloaded_artifact_descriptor,
                artifact_source_for_path=self._artifact_source_for_path,
                produced_by=self.__class__.__name__,
            )
            # Per-paper download cost telemetry (real OpenRouter cost + tokens of
            # all browser-use LLM steps + wall time), recorded once whether the
            # run succeeded or errored — the LLM cost was incurred either way.
            if download_llm is not None and self.dao is not None:
                try:
                    meta = download_llm.cost_meter.snapshot()
                    if download_started_at is not None:
                        meta["duration_ms"] = int((time.monotonic() - download_started_at) * 1000)
                    self.dao.record_stage_cost(
                        self.project_id, paper_name, "download", meta
                    )
                except Exception as cost_err:
                    pipeline_log(
                        f"download cost telemetry record failed for {paper_name}: {cost_err}",
                        stage="download", team=self.team_name,
                        project_id=self.project_id, level=logging.WARNING,
                    )

        try:
            if run_error is not None:
                raise run_error

            coverage = infer_coverage_for_artifacts(
                artifacts=produced_artifacts,
                plan=plan,
                task_override=task_override,
            )
            return AcquisitionTaskResult(
                task_type="repository_download",
                status="completed" if produced_artifacts or dataset_count > 0 else "skipped",
                produced_artifacts=produced_artifacts,
                coverage=coverage,
                confidence=1.0 if produced_artifacts else None,
                remaining_gaps=[] if coverage.sequencing_data and coverage.drug_response_data else list(plan.blocking_gaps),
                message=(
                    f"Recorded {len(produced_artifacts)} downloadable artifact(s)"
                    if produced_artifacts
                    else "Repository download produced no files"
                ),
                metadata={
                    "paper": paper_name,
                    "primary_strategy": plan.primary_strategy,
                    "task_title": task_override.title if task_override else None,
                },
            ).model_dump()
        finally:
            self._cleanup_profile_path(browser_profile_path)

    async def cleanup(self):
        pipeline_log(
            f"{self.__class__.__name__} cleaned up",
            stage="download",
            team=self.team_name,
            project_id=self.project_id,
        )
        pass


DownloadTeam = WebsiteDownloadTeam

"""
Integration layer between the web UI backend and the existing orchestrator logic.
"""

import asyncio
import json
import logging
import sys
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime

from models import PipelineStage, StageStatus, MessageType, WebSocketMessage
from websocket_handler import WebSocketHandler

project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
    logging.getLogger(__name__).info(f"Added project root to Python path: {project_root}")

from utils.path_utils import get_browseruse_datasets_dir
from utils.pipeline_log import pipeline_log
from utils.pipeline_messages import pm

logger = logging.getLogger(__name__)


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


def qualify_enabled() -> bool:
    """Whether the qualify stage should run. Default OFF.

    The qualify stage shells out to the Claude CLI, which is not installed on
    the auto-deploy host. Gate it behind QUALIFY_ENABLED so production behaves
    exactly as before this stage existed (find→analyze→download→complete) until
    Claude Code is installed and authenticated on the host.
    """
    return _truthy_env("QUALIFY_ENABLED")


def pipeline_parallel_enabled(db_row: Optional[Dict[str, Any]] = None) -> bool:
    """Single source of truth: the PIPELINE_PARALLEL env var.

    The per-project ``parallel_pipeline`` column is no longer consulted —
    the create-project UI used to expose a checkbox that wrote it, but
    that knob was removed (2026-05) because mixed-mode (some projects
    parallel, others sequential) didn't make operational sense.
    Operators now set ``PIPELINE_PARALLEL=true`` on the backend to opt
    everything in. The DB column and request field remain for
    back-compat but are ignored by this helper.
    """
    return _truthy_env("PIPELINE_PARALLEL")


def pipeline_max_parallel_tasks() -> int:
    raw = os.environ.get("PIPELINE_MAX_PARALLEL", "4").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 4


# Analysis-classification taxonomy used to drive resume behaviour.
#
# SUITABLE — the analysis found actionable gene-expression + drug-response data
# the download router can act on. Such artifacts are treated as "done" by the
# analyze stage on resume and forwarded to the download stage.
#
# RETRY    — the analysis exists but the classification says the work is not
# usable: either the LLM said no data was found (no_analysis), the paper needs
# author contact (contact_author), or the plan failed validation requiring
# human review (manual_required). On resume the analyze stage MUST re-attempt
# these so an improved extraction (e.g. MinerU quota restored) gets another
# shot, and the download stage MUST NOT process them — they should go to the
# human review queue instead.
SUITABLE_CLASSIFICATIONS = frozenset({"both_data", "sequencing_only", "drug_only"})
RETRY_CLASSIFICATIONS = frozenset({"manual_required", "contact_author", "no_analysis"})

# Marker analyze/team.py injects at the top of a markdown when the LLM response
# failed to parse and the raw output was preserved for manual review. Treated
# as an independent retry signal: even if AnalysisParser happens to classify a
# degraded artifact as something OTHER than RETRY_CLASSIFICATIONS (e.g. the raw
# LLM output coincidentally contains a suitability/downloadable line that
# pattern-matches), the presence of this marker means the structured analysis
# is missing and the paper deserves another extraction attempt on resume.
DEGRADED_MARKDOWN_MARKER = "Auto-analysis degraded"


def _content_looks_degraded(content: Optional[str]) -> bool:
    """True iff the analysis markdown carries the degraded warning header in
    its first 500 chars (the header is always written at the very top, so a
    short window avoids false positives from quoted text further down)."""
    if not content:
        return False
    return DEGRADED_MARKDOWN_MARKER in content[:500]


def make_download_ui_callback(web_ui: "WebOrchestratorUI"):
    """Forward DownloadTeam status lines to the web status strip (sync → async)."""

    def _cb(kind: str, message: str) -> None:
        if kind != "status":
            return
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(web_ui.update_status(message, "system"))
        except RuntimeError:
            pass

    return _cb


class WebOrchestratorUI:
    """Web-based orchestrator UI bridge (WebSocket + pipeline state)."""

    def __init__(self, project_id: str, websocket_handler: WebSocketHandler):
        self.project_id = project_id
        self.websocket_handler = websocket_handler
        self._current_stage = "Initializing"
        self._progress = 0.0

    def start(self):
        pipeline_log(
            "web ui bridge started",
            stage="pipeline",
            component="web_ui_bridge",
            project_id=self.project_id,
        )

    def stop(self):
        pipeline_log(
            "web ui bridge stopped",
            stage="pipeline",
            component="web_ui_bridge",
            project_id=self.project_id,
        )

    async def update_stage(self, stage_name: str):
        self._current_stage = stage_name

        pipeline_stage = self._parse_stage_name(stage_name)
        if pipeline_stage:
            await self.websocket_handler.update_project_stage(
                self.project_id,
                pipeline_stage,
                StageStatus.IN_PROGRESS
            )

    async def update_progress(self, progress: float):
        self._progress = progress

        pipeline_stage = self._parse_stage_name(self._current_stage)
        if pipeline_stage:
            await self.websocket_handler.update_project_progress(
                self.project_id,
                pipeline_stage,
                progress
            )

    async def update_status(self, status: str, status_type: str = "system"):
        await self.websocket_handler.update_project_status(
            self.project_id,
            status,
            status_type
        )

    async def push_message(
        self,
        message: str,
        team_name: str = None,
        message_type: str = "system",
        persist: bool = True,
    ):
        msg_type = MessageType(message_type) if message_type in MessageType._value2member_map_ else MessageType.SYSTEM

        await self.websocket_handler.add_project_message(
            self.project_id,
            message,
            msg_type,
            team_name,
            persist=persist,
        )

    async def push_team_message(
        self,
        team_name: str,
        message: str,
        message_type: str = "system",
        persist: bool = True,
    ):
        await self.push_message(
            message,
            team_name=team_name,
            message_type=message_type,
            persist=persist,
        )

    async def update_thinking(self, thinking: str, agent_name: str = None):
        await self.websocket_handler.update_thinking(
            self.project_id,
            thinking,
            agent_name
        )

    async def request_input(self, prompt: str = "Please provide input:", team_name: str = "System") -> str:
        await self.push_message(f"HUMAN INPUT NEEDED: {prompt}", team_name=team_name, message_type="info")

        response = await self.websocket_handler.request_user_input(
            self.project_id,
            prompt,
            team_name=team_name
        )

        await self.push_message(f"User response: {response}", team_name=team_name, message_type="info")

        return response

    def _parse_stage_name(self, stage_name: str) -> Optional[PipelineStage]:
        stage_lower = stage_name.lower()

        if "find" in stage_lower:
            return PipelineStage.FIND
        elif "analy" in stage_lower:
            return PipelineStage.ANALYZE
        elif "download" in stage_lower:
            return PipelineStage.DOWNLOAD
        elif "qualif" in stage_lower:
            return PipelineStage.QUALIFY
        elif "complete" in stage_lower:
            return PipelineStage.COMPLETE

        return None


_current_ui: Optional[WebOrchestratorUI] = None


def set_ui(ui: Optional[WebOrchestratorUI]):
    global _current_ui
    _current_ui = ui


def get_ui() -> Optional[WebOrchestratorUI]:
    return _current_ui


async def push_message(
    message: str,
    team_name: str = None,
    message_type: str = "system",
    persist: bool = True,
):
    ui = get_ui()
    if ui:
        await ui.push_team_message(team_name, message, message_type, persist=persist)


async def update_stage(stage_name: str):
    ui = get_ui()
    if ui:
        await ui.update_stage(stage_name)


async def update_progress(progress: float):
    ui = get_ui()
    if ui:
        await ui.update_progress(progress)


async def update_status(status: str, status_type: str = "system"):
    ui = get_ui()
    if ui:
        await ui.update_status(status, status_type)


class PipelineRunner:
    """Runs the research pipeline for a specific project using the web UI."""

    def __init__(self, project_id: str, websocket_handler: WebSocketHandler, cancellation_flags: Dict[str, bool] = None):
        self.project_id = project_id
        self.websocket_handler = websocket_handler
        self.ui = WebOrchestratorUI(project_id, websocket_handler)
        self.cancellation_flags = cancellation_flags or {}

        from database.dao import ProjectDAO
        self.dao = ProjectDAO()
        set_ui(self.ui)

        from storage import get_storage
        self.storage = get_storage()

        self.find_team = None
        self.analysis_team = None
        self.download_team = None

    async def _store_analysis_artifact_upload(self, analysis: Dict[str, Any]) -> None:
        notes_path = analysis.get("notes_path")
        if not notes_path or not os.path.exists(notes_path):
            return
        storage_key = f"analyses/{os.path.basename(notes_path)}"
        storage_result = None
        try:
            storage_result = self.storage.upload(local_path=notes_path, key=storage_key)
            pipeline_log(
                f"analysis artifact uploaded: {storage_key}",
                stage="analyze",
                team="PipelineRunner",
                project_id=self.project_id,
            )
        except Exception as e:
            pipeline_log(
                f"analysis artifact upload failed: {e}",
                stage="analyze",
                team="PipelineRunner",
                project_id=self.project_id,
                level=logging.WARNING,
            )

        try:
            self.dao.add_artifact(
                project_id=self.project_id,
                artifact_type="analysis",
                file_path=notes_path,
                stage_name="analyze",
                storage_metadata={
                    "storage_backend": storage_result.storage_backend,
                    "s3_bucket": storage_result.s3_bucket,
                    "s3_key": storage_result.s3_key,
                    "oss_bucket": storage_result.oss_bucket,
                    "oss_key": storage_result.oss_key,
                } if storage_result else None,
            )
            pipeline_log(
                f"analysis artifact recorded: {storage_key}",
                stage="analyze",
                team="PipelineRunner",
                project_id=self.project_id,
            )
        except Exception as e:
            pipeline_log(
                f"analysis artifact DB record failed: {e}",
                stage="analyze",
                team="PipelineRunner",
                project_id=self.project_id,
                level=logging.ERROR,
            )

    def _extend_with_cross_project_analyses(
        self, papers: List[str], existing_by_stem: Dict[str, Any]
    ) -> int:
        """Best-effort: for each paper whose analysis is NOT yet recorded in
        the current project, look up another project's analysis with the same
        filename stem and "borrow" it by inserting an analysis artifact row
        for the current project pointing at the same .md on disk.

        Mutates ``existing_by_stem`` in place by adding any reused entries.
        Returns the number of analyses actually reused.

        The analyze stage is otherwise idempotent only inside a single
        project — without this hop, two projects whose Find stages converged
        on the same paper file each re-run MinerU + LLM, wasting both wall
        time and OpenRouter spend.
        """
        if not papers:
            return 0
        candidate_stems = [
            os.path.splitext(os.path.basename(p))[0] for p in papers
        ]
        candidate_stems = [s for s in candidate_stems if s and s not in existing_by_stem]
        if not candidate_stems:
            return 0

        reusable = self.dao.find_reusable_analyses_by_stems(
            exclude_project_id=self.project_id,
            paper_stems=candidate_stems,
        )
        if not reusable:
            return 0

        reused = 0
        for stem, info in reusable.items():
            file_path = info.get("file_path")
            if not file_path or not os.path.exists(file_path):
                continue
            try:
                self.dao.add_artifact(
                    project_id=self.project_id,
                    artifact_type="analysis",
                    file_path=file_path,
                    stage_name="analyze",
                )
            except Exception as e:
                pipeline_log(
                    f"analyze: failed to record cross-project reuse for {stem!r}: {e}",
                    stage="analyze",
                    team="PipelineRunner",
                    project_id=self.project_id,
                    level=logging.WARNING,
                )
                continue

            candidate_plan_path = str(Path(file_path).with_suffix(".plan.json"))
            plan_exists = os.path.isfile(candidate_plan_path)
            flag = info.get("data_classification_flag")
            if flag in RETRY_CLASSIFICATIONS:
                has_suitable = "Manual"
            else:
                has_suitable = "Yes" if plan_exists else "Manual"
            existing_by_stem[stem] = {
                "title": os.path.basename(file_path),
                "has_suitable_data": has_suitable,
                "notes_path": file_path,
                "plan_path": candidate_plan_path if plan_exists else None,
                "data_classification_flag": flag,
            }
            reused += 1
            src_pid = (info.get("source_project_id") or "")[:8]
            pipeline_log(
                f"analyze: reusing analysis across projects (src={src_pid}) for stem={stem!r}",
                stage="analyze",
                team="PipelineRunner",
                project_id=self.project_id,
            )
        return reused

    def _load_analyses_from_artifacts(self) -> List[Dict[str, Any]]:
        analysis_artifacts = self.dao.get_project_artifacts(self.project_id, "analysis")
        analyses: List[Dict[str, Any]] = []
        for artifact in analysis_artifacts:
            notes_path = artifact.get("file_path")
            candidate_plan_path = str(Path(notes_path).with_suffix(".plan.json")) if notes_path else None
            # Only expose plan_path when the file actually exists; degraded analyses
            # never produced a plan.json and passing None to the download router is safe
            # (the entry gets filtered as "Manual" and skipped by the download stage).
            plan_exists = candidate_plan_path and Path(candidate_plan_path).is_file()
            plan_path = candidate_plan_path if plan_exists else None
            # data_classification_flag wins over plan-on-disk: a manual_required
            # analysis can still have a structurally-valid plan.json that survived
            # validation, but the markdown semantically says "no usable data" — we
            # must NOT push it into download. See orchestrator_integration tests.
            flag = artifact.get("data_classification_flag")
            is_degraded = _content_looks_degraded(artifact.get("file_content"))
            # An artifact gets the RETRY treatment (Manual + excluded from
            # skip-set) when EITHER its classification flag says so OR the
            # markdown carries the degraded-parse warning header. The two
            # signals are independent: classification can land outside
            # RETRY_CLASSIFICATIONS for a degraded artifact if the raw LLM
            # output coincidentally pattern-matches a suitable verdict.
            if flag in RETRY_CLASSIFICATIONS or is_degraded:
                has_suitable = "Manual"
            else:
                has_suitable = "Yes" if plan_exists else "Manual"
            analyses.append(
                {
                    "title": artifact.get("file_name") or notes_path,
                    "has_suitable_data": has_suitable,
                    "notes_path": notes_path,
                    "plan_path": plan_path,
                    "data_classification_flag": flag,
                    "is_degraded": is_degraded,
                }
            )
        return analyses

    async def _analyze_papers_parallel(
        self,
        papers: List[str],
        *,
        project_total: Optional[int] = None,
        baseline_done: int = 0,
    ) -> List[Dict[str, Any]]:
        """Parallel analyze loop.

        ``papers`` is the list of papers that still need fresh analysis (the
        callers strip out the skip-set first). For the progress fraction
        we want the project-wide view, not "0/24 → 1/24 → ..." over just
        this resume's residual workload. Two extra kwargs:

          project_total — total paper count in the project (the analyze
              stage's full denominator). Defaults to ``len(papers)`` for the
              legacy "no skip set" callers.
          baseline_done — count of papers already done before this run
              kicked off (i.e. the skip-set size). Defaults to 0.

        Progress reported = (baseline_done + completed_this_run) / project_total.
        """
        from analyze.team import AnalysisTeam

        max_p = pipeline_max_parallel_tasks()
        sem = asyncio.Semaphore(max_p)
        progress_lock = asyncio.Lock()
        n = len(papers)
        completed = 0
        total_denominator = project_total if project_total and project_total > 0 else n
        # Clamp baseline so unexpected drift (e.g. baseline_done > total)
        # never yields a > 1.0 fraction that would confuse the frontend.
        baseline_done = max(0, min(baseline_done, total_denominator))

        pipeline_log(
            f"analyze parallel: concurrency cap={max_p} papers={n} "
            f"baseline_done={baseline_done} project_total={total_denominator}",
            stage="analyze",
            team="PipelineRunner",
            project_id=self.project_id,
        )

        _per_paper_timeout = float(os.getenv("ANALYZE_PER_PAPER_TIMEOUT_SECONDS") or "900")

        # fetch project meta once so pmid + force_reanalyze
        # are available for every paper analysed in this batch.
        project_meta_local = self.dao.get_project(self.project_id) or {}
        force_reanalyze_flag = bool(project_meta_local.get("force_reanalyze"))

        async def run_one(paper_path: str) -> Optional[Dict[str, Any]]:
            nonlocal completed
            async with sem:
                self._check_cancellation()
                team = AnalysisTeam(team_name="AnalysisTeam", project_id=self.project_id,
                                   model_override=getattr(self, "_run_analysis_model", None))
                # cache hookup needs PMID per paper.
                paper_pmid = self.dao.get_paper_pmid_by_path(self.project_id, paper_path)
                try:
                    pipeline_log(
                        f"analyze (parallel): path={paper_path} pmid={paper_pmid} "
                        f"force_reanalyze={force_reanalyze_flag}",
                        stage="analyze",
                        team="PipelineRunner",
                        project_id=self.project_id,
                    )
                    try:
                        analysis = await asyncio.wait_for(
                            team.run(
                                paper_path,
                                pmid=paper_pmid,
                                force_reanalyze=force_reanalyze_flag,
                            ),
                            timeout=_per_paper_timeout,
                        )
                    except asyncio.TimeoutError:
                        pipeline_log(
                            f"analyze (parallel): {paper_path!r} timed out after {_per_paper_timeout:.0f}s → skipping",
                            stage="analyze",
                            team="PipelineRunner",
                            project_id=self.project_id,
                            level=logging.WARNING,
                        )
                        self._log_execution(
                            event_type="paper_analyze_skipped",
                            message=f"analyze timeout {_per_paper_timeout:.0f}s: {paper_path}",
                            stage_name="analyze",
                            payload={"paper": paper_path, "outcome": "timeout",
                                     "timeout_seconds": _per_paper_timeout},
                            severity="warning",
                        )
                        analysis = None
                    if analysis:
                        await self._store_analysis_artifact_upload(analysis)
                    else:
                        reason = getattr(team, "last_failure_reason", None) or "unknown"
                        self._log_execution(
                            event_type="paper_analyze_skipped",
                            message=f"analyze returned no artifact ({reason}): {paper_path}",
                            stage_name="analyze",
                            payload={"paper": paper_path, "outcome": reason,
                                     "pmid": paper_pmid},
                            severity="info" if reason == "no_suitable_data" else "warning",
                        )
                    async with progress_lock:
                        completed += 1
                        # Use the project-wide denominator so resume runs
                        # show "已分析 / 项目论文数", not "本次跑了 / 本次要跑".
                        # See _analyze_papers_parallel docstring.
                        fraction = (baseline_done + completed) / total_denominator
                        await self.websocket_handler.update_project_progress(
                            self.project_id, PipelineStage.ANALYZE,
                            min(1.0, fraction),
                        )
                    return analysis
                finally:
                    await team.cleanup()

        tasks = [run_one(p) for p in papers]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        analyses: List[Dict[str, Any]] = []
        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                pipeline_log(
                    f"Analysis of {papers[i]} failed: {result}",
                    stage="analyze",
                    team="PipelineRunner",
                    project_id=self.project_id,
                    level=logging.ERROR,
                )
                self._log_execution(
                    event_type="paper_analyze_skipped",
                    message=f"analyze raised {type(result).__name__}: {papers[i]}",
                    stage_name="analyze",
                    payload={"paper": papers[i], "outcome": "exception",
                             "exc_type": type(result).__name__, "exc_msg": str(result)[:500]},
                    severity="error",
                )
            elif result:
                analyses.append(result)
        return analyses

    async def _download_suitable_parallel(
        self,
        suitable_analyses: List[Dict[str, Any]],
        *,
        project_total: Optional[int] = None,
        baseline_done: int = 0,
    ) -> Tuple[List[Any], List[str]]:
        """Parallel download loop. See _analyze_papers_parallel for the
        same baseline/total reasoning — on resume we want
        "已完成 / 项目应下载总数", not "本轮完成 / 本轮待下载"."""
        from download.router import AcquisitionRouter

        max_p = pipeline_max_parallel_tasks()
        sem = asyncio.Semaphore(max_p)
        progress_lock = asyncio.Lock()
        n = len(suitable_analyses)
        completed = 0
        total_denominator = project_total if project_total and project_total > 0 else n
        baseline_done = max(0, min(baseline_done, total_denominator))

        pipeline_log(
            f"download parallel: concurrency cap={max_p} items={n} "
            f"baseline_done={baseline_done} project_total={total_denominator}",
            stage="download",
            team="PipelineRunner",
            project_id=self.project_id,
        )

        async def run_one(analysis: Dict[str, Any]) -> Any:
            nonlocal completed
            async with sem:
                self._check_cancellation()
                team = AcquisitionRouter(
                    team_name="AcquisitionRouter",
                    project_id=self.project_id,
                    ui_callback=make_download_ui_callback(self.ui),
                    cancellation_check=lambda: self.cancellation_flags.get(self.project_id, False),
                    download_model_override=getattr(self, "_run_download_model", None),
                )
                try:
                    pipeline_log(
                        f"download (parallel): title={analysis.get('title')!r} "
                        f"plan_path={analysis.get('plan_path')!r}",
                        stage="download",
                        team="PipelineRunner",
                        project_id=self.project_id,
                    )
                    plan_path = analysis.get("plan_path")
                    download_result = await team.run(plan_path=plan_path)
                    try:
                        async with progress_lock:
                            completed += 1
                            fraction = (baseline_done + completed) / total_denominator
                            await self.websocket_handler.update_project_progress(
                                self.project_id, PipelineStage.DOWNLOAD,
                                min(1.0, fraction),
                            )
                    except Exception as prog_err:
                        pipeline_log(
                            f"download (parallel): progress update failed: {prog_err}",
                            stage="download",
                            team="PipelineRunner",
                            project_id=self.project_id,
                            level=logging.WARNING,
                        )
                    return download_result
                finally:
                    await team.cleanup()

        results = await asyncio.gather(
            *[run_one(a) for a in suitable_analyses], return_exceptions=True
        )
        downloads: List[Any] = []
        failed_titles: List[str] = []
        for i, result in enumerate(results):
            title = suitable_analyses[i].get('title') or suitable_analyses[i].get('plan_path') or f"item_{i + 1}"
            if isinstance(result, BaseException):
                failed_titles.append(str(title))
                pipeline_log(
                    f"Download for {title} failed: {result}",
                    stage="download",
                    team="PipelineRunner",
                    project_id=self.project_id,
                    level=logging.ERROR,
                )
            elif result:
                downloads.append(result)
                pipeline_log(
                    f"download result for {title!r}: {result!r}",
                    stage="download",
                    team="PipelineRunner",
                    project_id=self.project_id,
                )
        await self._maybe_fetch_known_db(suitable_analyses)
        return downloads, failed_titles

    async def _maybe_fetch_known_db(self, suitable_analyses: List[Dict[str, Any]]) -> int:
        """Per-project once: when analyses reference a known DB with a stable
        linkable E+R endpoint (GDSC/CCLE/PRISM), fetch the canonical E+R
        directly — the only reliable source of a linkable triad (single papers
        almost never co-deposit one). Opt-in via KNOWN_DB_FETCH_ENABLED. Returns
        the number of dataset files registered. Best-effort: never raises."""
        from analyze.known_resources import resources_with_datasets
        from download.known_db_fetch import known_db_fetch_enabled
        from download.known_db_ingest import ingest_resource

        if not known_db_fetch_enabled():
            return 0
        try:
            texts: List[str] = []
            for row in self.dao.get_project_artifacts(self.project_id, artifact_type="analysis"):
                if row.get("file_content"):
                    texts.append(row["file_content"])
            for a in suitable_analyses:
                if a.get("title"):
                    texts.append(a["title"])
            resources = resources_with_datasets("\n".join(texts))
            if not resources:
                return 0
            registered = 0
            for resource in resources:
                registered += ingest_resource(
                    self.dao, self.project_id, resource, team="PipelineRunner"
                )
            return registered
        except Exception as e:
            pipeline_log(
                f"known_db fetch failed: {type(e).__name__}: {e}",
                stage="download", team="PipelineRunner",
                project_id=self.project_id, level=logging.WARNING,
            )
            return 0

    def _check_cancellation(self):
        is_cancelled = self.cancellation_flags.get(self.project_id, False)
        if is_cancelled:
            pipeline_log(
                "cancellation requested via project flag",
                stage="pipeline",
                team="PipelineRunner",
                project_id=self.project_id,
            )
            raise asyncio.CancelledError()

    async def run_full_pipeline(
        self,
        initial_query: str,
        resume: bool = False,
        max_papers: int = 10,
        parallel: bool = False,
        analysis_model: Optional[str] = None,
        download_model: Optional[str] = None,
        date_start: Optional[str] = None,
        date_end: Optional[str] = None,
    ) -> None:
        # Store per-run model + date overrides so helper methods can access them.
        self._run_analysis_model: Optional[str] = analysis_model
        self._run_download_model: Optional[str] = download_model
        # date range threaded through from start_pipeline.
        self._run_date_start: Optional[str] = date_start
        self._run_date_end: Optional[str] = date_end

        mode = "resume" if resume else "start"
        if not resume:
            self._log_message("pipeline", pm("pipeline.start_requested", mode=mode), "info")
            self._log_execution(
                event_type="pipeline_start",
                message=f"Pipeline {mode} requested",
                stage_name="pipeline",
                payload={"mode": mode, "max_papers": max_papers, "parallel": parallel},
            )
        pipeline_log(
            f"pipeline {mode}: query={initial_query!r} max_papers={max_papers} parallel={parallel} "
            f"analysis_model={analysis_model!r} download_model={download_model!r} "
            f"date_start={date_start!r} date_end={date_end!r}",
            stage="pipeline",
            team="PipelineRunner",
            project_id=self.project_id,
        )

        try:
            self._check_cancellation()

            stages_to_run = {"find": True, "analyze": True, "download": True, "qualify": True}
            papers = []
            analyses = []
            downloads: List[Any] = []

            if resume:
                completed_stages = self.dao.get_project_stages(self.project_id)
                pipeline_log(
                    f"resume: loaded {len(completed_stages)} stage row(s) from database",
                    stage="pipeline",
                    team="PipelineRunner",
                    project_id=self.project_id,
                )

                # Rows are ordered by start_time ascending; multiple rows per stage_name are possible
                # after incremental imports (e.g. a new paused download row after a completed run).
                # Only the latest row per stage_name should decide whether we skip — matching
                # pause_stage / update_stage_progress_by_name behavior.
                latest_stage_by_name: Dict[str, Dict[str, Any]] = {}
                for stage_record in completed_stages:
                    latest_stage_by_name[str(stage_record["stage_name"])] = stage_record

                for name in ("find", "analyze", "download", "qualify"):
                    rec = latest_stage_by_name.get(name)
                    if rec and rec.get("status") == "completed":
                        stages_to_run[name] = False
                        pipeline_log(
                            f"resume: stage {name!r} already completed → will skip this stage",
                            stage="pipeline",
                            team="PipelineRunner",
                            project_id=self.project_id,
                        )

                if not stages_to_run["find"]:
                    artifacts = self.dao.get_project_artifacts(self.project_id, "paper")
                    papers = [a["file_path"] for a in artifacts]
                    pipeline_log(
                        f"resume: reusing {len(papers)} paper path(s) from artifacts (find skipped)",
                        stage="find",
                        team="PipelineRunner",
                        project_id=self.project_id,
                    )

                if not stages_to_run["analyze"]:
                    analysis_artifacts = self.dao.get_project_artifacts(self.project_id, "analysis")
                    pipeline_log(
                        f"resume: found {len(analysis_artifacts)} analysis artifact(s) (analyze may skip)",
                        stage="analyze",
                        team="PipelineRunner",
                        project_id=self.project_id,
                    )

            pipeline_log(
                f"execution plan: find={stages_to_run['find']} analyze={stages_to_run['analyze']} "
                f"download={stages_to_run['download']} qualify={stages_to_run['qualify']}",
                stage="pipeline",
                team="PipelineRunner",
                project_id=self.project_id,
            )

            await self.ui.update_stage("Pipeline - Starting")

            if stages_to_run["find"]:
                stage_id = self.dao.get_or_start_stage(self.project_id, "find")
                if stage_id is None:
                    pipeline_log(
                        "find stage already marked complete in DB → loading papers from artifacts only",
                        stage="find",
                        team="PipelineRunner",
                        project_id=self.project_id,
                    )
                    stages_to_run["find"] = False
                    artifacts = self.dao.get_project_artifacts(self.project_id, "paper")
                    papers = [a['file_path'] for a in artifacts]

            if stages_to_run["find"]:
                self._log_message("find", pm("pipeline.find_started"), "info")
                self._log_execution(
                    event_type="stage_start",
                    message="Find stage started",
                    stage_name="find",
                    payload={"max_papers": max_papers},
                )
                pipeline_log(
                    "begin find: Semantic Scholar search + OA waterfall downloads "
                    f"(strategy=open_access_only first, supplement non-OA if pool thin; see find logs)",
                    stage="find",
                    team="PipelineRunner",
                    project_id=self.project_id,
                )
                await self.ui.update_stage("Stage 1 - Finding Papers")
                await self.ui.update_status("Searching for and downloading research papers", "system")

                await self.websocket_handler.update_project_stage(
                    self.project_id, PipelineStage.FIND, StageStatus.IN_PROGRESS
                )

                self._check_cancellation()

                try:
                    from find.team import FindTeam
                except ImportError as e:
                    pipeline_log(
                        f"import failure: FindTeam ({e}); cwd={os.getcwd()}",
                        stage="find",
                        team="PipelineRunner",
                        project_id=self.project_id,
                        level=logging.ERROR,
                    )
                    logger.exception("Failed to import FindTeam")
                    raise
                # thread user_email so FindTeam's fill-to-target
                # can dedup against the user's other projects via batched DAO.
                project_meta = self.dao.get_project(self.project_id) or {}
                user_email = project_meta.get("user_email")
                self.find_team = FindTeam(
                    team_name="FindTeam",
                    websocket_handler=self.websocket_handler,
                    project_id=self.project_id,
                    user_email=user_email,
                )
                # forward the persisted date range so PubMed
                # ESearch / S2 (legacy) get the mindate/maxdate params. None
                # falls through to FindTeam.run() defaults.
                find_kwargs = {"max_papers": max_papers}
                if date_start:
                    find_kwargs["date_start"] = date_start
                if date_end:
                    find_kwargs["date_end"] = date_end
                papers = await self.find_team.run(initial_query, **find_kwargs)

                self._check_cancellation()

                if not papers:
                    self._log_message(
                        "find",
                        pm("pipeline.find_none", query=initial_query),
                        "error",
                    )
                    pipeline_log(
                        "find aborted: no papers returned by FindTeam",
                        stage="find",
                        team="PipelineRunner",
                        project_id=self.project_id,
                        level=logging.ERROR,
                    )
                    await self.ui.update_status("No papers found. Pipeline aborted.", "error")
                    await self.websocket_handler.update_project_stage(
                        self.project_id, PipelineStage.FIND, StageStatus.ERROR
                    )
                    return

                self._log_message(
                    "find",
                    pm("pipeline.find_success", count=len(papers)),
                    "info",
                )
                self._log_execution(
                    event_type="stage_complete",
                    message=f"Find stage completed with {len(papers)} papers",
                    stage_name="find",
                    payload={"paper_count": len(papers)},
                )
                pipeline_log(
                    f"find finished: {len(papers)} paper path(s); artifacts recorded by FindTeam during download",
                    stage="find",
                    team="PipelineRunner",
                    project_id=self.project_id,
                )

                await self.websocket_handler.update_project_stage(
                    self.project_id, PipelineStage.FIND, StageStatus.COMPLETED, 1.0
                )
                await self.ui.update_status(f"Found {len(papers)} papers", "system")

                self.dao.complete_stage(stage_id, status='completed')

                # PAUSE_AFTER_FIND: when set, end the pipeline run here so the
                # user can review the downloaded papers before the (more
                # expensive) analyze + download stages begin. Resume picks
                # up at analyze because find's stage row is now `completed`,
                # so run_full_pipeline's stages_to_run dict skips it.
                #
                # Only triggers when find JUST ran in this invocation — the
                # else-branch (find loaded from a previous run) skips this
                # block on purpose, otherwise resume would loop in paused
                # state forever.
                if os.getenv("PAUSE_AFTER_FIND", "").strip().lower() in {"1", "true", "yes", "on"}:
                    pipeline_log(
                        "find completed; pausing per PAUSE_AFTER_FIND env flag",
                        stage="pipeline",
                        team="PipelineRunner",
                        project_id=self.project_id,
                    )
                    self._log_message(
                        "pipeline",
                        pm("pipeline.paused_after_find", count=len(papers)),
                        "info",
                    )
                    self._log_execution(
                        event_type="pipeline_paused",
                        message="Auto-paused after find per PAUSE_AFTER_FIND",
                        stage_name="pipeline",
                        payload={"paper_count": len(papers)},
                    )
                    self.dao.update_project_status(self.project_id, "paused")
                    if self.websocket_handler:
                        await self.websocket_handler.broadcast_message(WebSocketMessage(
                            type="project_status_changed",
                            project_id=self.project_id,
                            data={"status": "paused"},
                        ))
                    return
            else:
                pipeline_log(
                    f"find skipped (already done): {len(papers)} paper path(s) in hand",
                    stage="find",
                    team="PipelineRunner",
                    project_id=self.project_id,
                )
                await self.websocket_handler.update_project_stage(
                    self.project_id, PipelineStage.FIND, StageStatus.COMPLETED, 1.0
                )
                await self.ui.update_status(f"Loaded {len(papers)} papers from previous run", "system")

            self._check_cancellation()

            if stages_to_run["analyze"]:
                stage_id = self.dao.get_or_start_stage(self.project_id, "analyze")
                if stage_id is None:
                    pipeline_log(
                        "analyze stage already complete in DB → building analysis list from artifacts",
                        stage="analyze",
                        team="PipelineRunner",
                        project_id=self.project_id,
                    )
                    stages_to_run["analyze"] = False
                    analyses = self._load_analyses_from_artifacts()

            if stages_to_run["analyze"]:
                self._log_message("analyze", pm("pipeline.analyze_started"), "info")
                self._log_execution(
                    event_type="stage_start",
                    message="Analyze stage started",
                    stage_name="analyze",
                    payload={"paper_count": len(papers), "parallel": parallel},
                )
                pipeline_log(
                    f"begin analyze: {len(papers)} PDF(s); extraction order MinerU (if token) "
                    f"then pdfplumber fallback; LLM multimodal with OpenAI SDK fallback on autogen errors",
                    stage="analyze",
                    team="PipelineRunner",
                    project_id=self.project_id,
                )
                await self.ui.update_stage("Stage 2 - Analyzing Papers")
                await self.ui.update_status("Analyzing papers for data suitability", "system")

                await self.websocket_handler.update_project_stage(
                    self.project_id, PipelineStage.ANALYZE, StageStatus.IN_PROGRESS
                )

                self._check_cancellation()

                try:
                    from analyze.team import AnalysisTeam
                except ImportError as e:
                    pipeline_log(
                        f"import failure: AnalysisTeam ({e}); cwd={os.getcwd()}",
                        stage="analyze",
                        team="PipelineRunner",
                        project_id=self.project_id,
                        level=logging.ERROR,
                    )
                    logger.exception("Failed to import AnalysisTeam")
                    raise
                self.analysis_team = None
                analyses = []

                if parallel:
                    # Per-paper resume: skip papers already recorded as artifacts on disk,
                    # EXCEPT when the classification says the work needs another attempt
                    # (manual_required / contact_author / no_analysis) OR the markdown
                    # is a degraded raw-LLM-output dump. Both signals drop the paper
                    # out of the skip set so analyze re-runs it with the current
                    # extractor + the stronger fallback model (if configured).
                    existing_by_stem_p: Dict[str, Any] = {
                        os.path.splitext(os.path.basename(a["notes_path"] or ""))[0]: a
                        for a in self._load_analyses_from_artifacts()
                        if a.get("notes_path")
                        and os.path.exists(a["notes_path"])
                        and a.get("data_classification_flag") not in RETRY_CLASSIFICATIONS
                        and not a.get("is_degraded", False)
                    }
                    reused_count_p = self._extend_with_cross_project_analyses(
                        papers, existing_by_stem_p
                    )
                    if existing_by_stem_p:
                        pipeline_log(
                            f"analyze (parallel): {len(existing_by_stem_p)} paper(s) reusable "
                            f"(this project + cross-project {reused_count_p}) — will skip",
                            stage="analyze",
                            team="PipelineRunner",
                            project_id=self.project_id,
                        )
                        analyses = list(existing_by_stem_p.values())
                        papers_to_analyze = [
                            p for p in papers
                            if os.path.splitext(os.path.basename(p))[0] not in existing_by_stem_p
                        ]
                    else:
                        papers_to_analyze = papers
                    if papers_to_analyze:
                        # Pass the project-wide denominator so progress
                        # reflects "已分析 / 全部 paper" instead of
                        # "本轮跑了 / 本轮要跑的".
                        analyses += await self._analyze_papers_parallel(
                            papers_to_analyze,
                            project_total=len(papers),
                            baseline_done=len(papers) - len(papers_to_analyze),
                        )
                else:
                    self.analysis_team = AnalysisTeam(
                        team_name="AnalysisTeam", project_id=self.project_id,
                        model_override=analysis_model,
                    )

                    # Per-paper resume: build a stem→analysis lookup from existing artifacts.
                    # Same RETRY_CLASSIFICATIONS + degraded-marker exclusion as the
                    # parallel path so manual_required / contact_author / no_analysis
                    # AND degraded-parse artifacts all re-run on resume.
                    existing_by_stem: Dict[str, Any] = {
                        os.path.splitext(os.path.basename(a["notes_path"] or ""))[0]: a
                        for a in self._load_analyses_from_artifacts()
                        if a.get("notes_path")
                        and os.path.exists(a["notes_path"])
                        and a.get("data_classification_flag") not in RETRY_CLASSIFICATIONS
                        and not a.get("is_degraded", False)
                    }
                    reused_count = self._extend_with_cross_project_analyses(
                        papers, existing_by_stem
                    )
                    if existing_by_stem:
                        pipeline_log(
                            f"analyze: {len(existing_by_stem)} paper(s) reusable "
                            f"(this project + cross-project {reused_count}) — will skip",
                            stage="analyze",
                            team="PipelineRunner",
                            project_id=self.project_id,
                        )

                    _per_paper_timeout = float(
                        os.getenv("ANALYZE_PER_PAPER_TIMEOUT_SECONDS") or "900"
                    )

                    # project meta once for the loop.
                    project_meta_seq = self.dao.get_project(self.project_id) or {}
                    force_reanalyze_seq = bool(project_meta_seq.get("force_reanalyze"))

                    for i, paper_path in enumerate(papers):
                        self._check_cancellation()
                        paper_stem = os.path.splitext(os.path.basename(paper_path))[0]

                        # Per-paper resume: skip if artifact already exists on disk
                        if paper_stem in existing_by_stem:
                            analyses.append(existing_by_stem[paper_stem])
                            pipeline_log(
                                f"analyze: skipping {paper_stem!r} — analysis artifact already exists",
                                stage="analyze",
                                team="PipelineRunner",
                                project_id=self.project_id,
                            )
                            await self.websocket_handler.update_project_progress(
                                self.project_id, PipelineStage.ANALYZE, (i + 1) / len(papers)
                            )
                            continue

                        # PMID lookup for cache.
                        paper_pmid = self.dao.get_paper_pmid_by_path(self.project_id, paper_path)

                        pipeline_log(
                            f"analyze: paper {i + 1}/{len(papers)} path={paper_path} "
                            f"pmid={paper_pmid} force_reanalyze={force_reanalyze_seq}",
                            stage="analyze",
                            team="PipelineRunner",
                            project_id=self.project_id,
                        )
                        try:
                            try:
                                analysis = await asyncio.wait_for(
                                    self.analysis_team.run(
                                        paper_path,
                                        pmid=paper_pmid,
                                        force_reanalyze=force_reanalyze_seq,
                                    ),
                                    timeout=_per_paper_timeout,
                                )
                            except asyncio.TimeoutError:
                                pipeline_log(
                                    f"analyze: paper {paper_path!r} timed out after "
                                    f"{_per_paper_timeout:.0f}s → skipping",
                                    stage="analyze",
                                    team="PipelineRunner",
                                    project_id=self.project_id,
                                    level=logging.WARNING,
                                )
                                self._log_execution(
                                    event_type="paper_analyze_skipped",
                                    message=f"analyze timeout {_per_paper_timeout:.0f}s: {paper_path}",
                                    stage_name="analyze",
                                    payload={"paper": paper_path, "outcome": "timeout",
                                             "timeout_seconds": _per_paper_timeout},
                                    severity="warning",
                                )
                                analysis = None
                            if analysis:
                                analyses.append(analysis)
                                await self._store_analysis_artifact_upload(analysis)
                            else:
                                reason = getattr(self.analysis_team, "last_failure_reason", None) or "unknown"
                                self._log_execution(
                                    event_type="paper_analyze_skipped",
                                    message=f"analyze returned no artifact ({reason}): {paper_path}",
                                    stage_name="analyze",
                                    payload={"paper": paper_path, "outcome": reason,
                                             "pmid": paper_pmid},
                                    severity="info" if reason == "no_suitable_data" else "warning",
                                )
                        except Exception as paper_exc:
                            pipeline_log(
                                f"analyze: unhandled error for {paper_path!r}: {paper_exc!r} → skipping",
                                stage="analyze",
                                team="PipelineRunner",
                                project_id=self.project_id,
                                level=logging.ERROR,
                            )
                            self._log_execution(
                                event_type="paper_analyze_skipped",
                                message=f"analyze raised {type(paper_exc).__name__}: {paper_path}",
                                stage_name="analyze",
                                payload={"paper": paper_path, "outcome": "exception",
                                         "exc_type": type(paper_exc).__name__,
                                         "exc_msg": str(paper_exc)[:500]},
                                severity="error",
                            )
                        finally:
                            await self.websocket_handler.update_project_progress(
                                self.project_id, PipelineStage.ANALYZE, (i + 1) / len(papers)
                            )

                suitable_count = sum(1 for a in analyses if a.get("has_suitable_data") == "Yes")
                pipeline_log(
                    f"analyze summary: {len(analyses)} result(s), {suitable_count} suitable (Yes/Partial) "
                    f"for download stage",
                    stage="analyze",
                    team="PipelineRunner",
                    project_id=self.project_id,
                )

                self._log_message(
                    "analyze",
                    pm(
                        "pipeline.analyze_summary",
                        papers=len(papers),
                        valid=len(analyses),
                        suitable=suitable_count,
                    ),
                    "info"
                )
                self._log_execution(
                    event_type="stage_complete",
                    message=f"Analyze stage completed with {len(analyses)} analyses",
                    stage_name="analyze",
                    payload={"analysis_count": len(analyses), "suitable_count": suitable_count},
                )

                if not analyses:
                    self._log_message("analyze", pm("pipeline.no_suitable"), "error")
                    pipeline_log(
                        "analyze aborted: no valid analysis outputs",
                        stage="analyze",
                        team="PipelineRunner",
                        project_id=self.project_id,
                        level=logging.ERROR,
                    )
                    await self.ui.update_status("No suitable papers found. Pipeline aborted.", "error")
                    await self.websocket_handler.update_project_stage(
                        self.project_id, PipelineStage.ANALYZE, StageStatus.ERROR
                    )
                    self.dao.complete_stage(stage_id, status='failed', error_message="No suitable papers found")
                    return

                await self.websocket_handler.update_project_stage(
                    self.project_id, PipelineStage.ANALYZE, StageStatus.COMPLETED, 1.0
                )
                await self.ui.update_status(f"Completed analysis of {len(analyses)} papers", "system")
                self.dao.complete_stage(stage_id, status='completed')
            else:
                pipeline_log(
                    "analyze skipped: using prior run (placeholder entries for download filtering may lack plan_path)",
                    stage="analyze",
                    team="PipelineRunner",
                    project_id=self.project_id,
                )
                analyses = self._load_analyses_from_artifacts()
                if not analyses:
                    analyses = [{"title": p, "has_suitable_data": "Yes"} for p in papers]
                await self.websocket_handler.update_project_stage(
                    self.project_id, PipelineStage.ANALYZE, StageStatus.COMPLETED, 1.0
                )
                await self.ui.update_status(f"Loaded {len(analyses)} analyses from previous run", "system")

            self._check_cancellation()

            if stages_to_run["download"]:
                stage_id = self.dao.get_or_start_stage(self.project_id, "download")
                if stage_id is None:
                    pipeline_log(
                        "download stage already complete in DB → skipping download body",
                        stage="download",
                        team="PipelineRunner",
                        project_id=self.project_id,
                    )
                    stages_to_run["download"] = False
                    downloads = []

            if stages_to_run["download"]:
                self._log_message("download", pm("pipeline.download_started"), "info")
                self._log_execution(
                    event_type="stage_start",
                    message="Download stage started",
                    stage_name="download",
                    payload={"parallel": parallel},
                )
                pipeline_log(
                    "begin download: browser-use agent, headless, 5min timeout per paper; "
                    "queue for large files; artifacts uploaded after run",
                    stage="download",
                    team="PipelineRunner",
                    project_id=self.project_id,
                )
                await self.ui.update_stage("Stage 3 - Downloading Datasets")
                await self.ui.update_status("Downloading datasets based on analysis", "system")

                await self.websocket_handler.update_project_stage(
                    self.project_id, PipelineStage.DOWNLOAD, StageStatus.IN_PROGRESS
                )

                self._check_cancellation()

                try:
                    from download.router import AcquisitionRouter
                except ImportError as e:
                    pipeline_log(
                        f"import failure: DownloadTeam ({e}); cwd={os.getcwd()}",
                        stage="download",
                        team="PipelineRunner",
                        project_id=self.project_id,
                        level=logging.ERROR,
                    )
                    logger.exception("Failed to import DownloadTeam")
                    raise
                self.download_team = None
                downloads = []
                download_failures: List[str] = []

                suitable_analyses = [a for a in analyses if a.get("has_suitable_data") in ["Yes", "Partial"]]
                # Denominator for the progress fraction = "all papers this
                # project intends to download". Captured BEFORE the
                # done_stems filter so the fraction stays project-wide
                # across resumes (was: each resume reset to "完成 / 本轮要跑").
                download_project_total = len(suitable_analyses)
                skipped = len(analyses) - len(suitable_analyses)
                pipeline_log(
                    f"download filter: {len(suitable_analyses)} suitable (Yes/Partial), {skipped} not suitable (excluded)",
                    stage="download",
                    team="PipelineRunner",
                    project_id=self.project_id,
                )

                # Resume-friendly per-paper skip: papers that already produced a
                # completed/partial/awaiting_external dataset artifact in a prior run
                # don't get re-acquired. Only ``no_valid_dataset_files`` (failed) and
                # never-attempted papers fall through to the router. Stale "no valid"
                # rows are cleaned up by router._cleanup_stale_no_valid_report when a
                # retry succeeds.
                done_stems = self.dao.get_paper_names_with_terminal_download(self.project_id)
                if done_stems:
                    before_n = len(suitable_analyses)
                    suitable_analyses = [
                        a for a in suitable_analyses
                        if os.path.splitext(os.path.basename(a.get("notes_path") or ""))[0]
                            not in done_stems
                    ]
                    pipeline_log(
                        f"download resume: skipping {before_n - len(suitable_analyses)} paper(s) "
                        f"with terminal download artifacts (completed/partial/awaiting_external); "
                        f"retrying {len(suitable_analyses)}",
                        stage="download",
                        team="PipelineRunner",
                        project_id=self.project_id,
                    )

                # Baseline = papers that were excluded by done_stems above (i.e.
                # already-completed in earlier resume runs). The new fraction is
                # (baseline_done + this_run_completed) / project_total.
                download_baseline_done = download_project_total - len(suitable_analyses)

                if suitable_analyses:
                    if parallel:
                        downloads, download_failures = await self._download_suitable_parallel(
                            suitable_analyses,
                            project_total=download_project_total,
                            baseline_done=download_baseline_done,
                        )
                    else:
                        self.download_team = AcquisitionRouter(
                            team_name="AcquisitionRouter",
                            project_id=self.project_id,
                            ui_callback=make_download_ui_callback(self.ui),
                            cancellation_check=lambda: self.cancellation_flags.get(self.project_id, False),
                            download_model_override=download_model,
                        )
                        for i, analysis in enumerate(suitable_analyses):
                            self._check_cancellation()

                            pipeline_log(
                                f"download: item {i + 1}/{len(suitable_analyses)} title={analysis.get('title')!r} "
                                f"plan_path={analysis.get('plan_path')!r}",
                                stage="download",
                                team="PipelineRunner",
                                project_id=self.project_id,
                            )
                            plan_path = analysis.get("plan_path")
                            download_result = await self.download_team.run(plan_path=plan_path)
                            if download_result:
                                downloads.append(download_result)
                                pipeline_log(
                                    f"download result for {analysis.get('title')!r}: {download_result!r}",
                                    stage="download",
                                    team="PipelineRunner",
                                    project_id=self.project_id,
                                )

                            # Project-wide denominator (same reasoning as the
                            # parallel path); avoids "重置回 0" appearance on resume.
                            fraction = (download_baseline_done + (i + 1)) / max(
                                1, download_project_total
                            )
                            await self.websocket_handler.update_project_progress(
                                self.project_id, PipelineStage.DOWNLOAD,
                                min(1.0, fraction),
                            )

                await self._maybe_fetch_known_db(suitable_analyses)

                if download_failures:
                    failed_preview = ", ".join(download_failures[:5])
                    if len(download_failures) > 5:
                        failed_preview += f", ... (+{len(download_failures) - 5} more)"
                    raise RuntimeError(
                        "Download stage completed with failures "
                        f"({len(download_failures)}/{len(suitable_analyses)} failed): {failed_preview}"
                    )

                dataset_artifact_count = len(
                    self.dao.get_project_artifacts(self.project_id, artifact_type="dataset")
                )

                await self.websocket_handler.update_project_stage(
                    self.project_id, PipelineStage.DOWNLOAD, StageStatus.COMPLETED, 1.0
                )
                self.dao.complete_stage(stage_id, status='completed')
                self._log_execution(
                    event_type="stage_complete",
                    message=f"Download stage completed with {dataset_artifact_count} results",
                    stage_name="download",
                    payload={"download_count": dataset_artifact_count},
                )
            else:
                pipeline_log("download skipped (already completed)", stage="download", team="PipelineRunner", project_id=self.project_id)
                await self.websocket_handler.update_project_stage(
                    self.project_id, PipelineStage.DOWNLOAD, StageStatus.COMPLETED, 1.0
                )
                await self.ui.update_status("Download stage already completed", "system")

            total_dataset_artifact_count = len(
                self.dao.get_project_artifacts(self.project_id, artifact_type="dataset")
            )

            # Stage 4: Qualify downloaded datasets . Runs AFTER
            # download; the pipeline-completion block below fires regardless of
            # the qualify outcome. Gated OFF by default — skipped cleanly
            # unless QUALIFY_ENABLED is set, so production behaves exactly as
            # before this stage existed.
            qualify_counts = None
            if not qualify_enabled():
                stages_to_run["qualify"] = False
                pipeline_log(
                    "qualify stage disabled (QUALIFY_ENABLED not set) → skipping qualify body",
                    stage="qualify",
                    team="PipelineRunner",
                    project_id=self.project_id,
                )
            if stages_to_run["qualify"]:
                stage_id = self.dao.get_or_start_stage(self.project_id, "qualify")
                if stage_id is None:
                    pipeline_log(
                        "qualify stage already complete in DB → skipping qualify body",
                        stage="qualify",
                        team="PipelineRunner",
                        project_id=self.project_id,
                    )
                    stages_to_run["qualify"] = False

            if stages_to_run["qualify"]:
                self._log_execution(
                    event_type="stage_start",
                    message="Qualify stage started",
                    stage_name="qualify",
                )
                await self.ui.update_stage("Stage 4 - Qualifying Datasets")
                await self.ui.update_status("Qualifying downloaded datasets", "system")
                await self.websocket_handler.update_project_stage(
                    self.project_id, PipelineStage.QUALIFY, StageStatus.IN_PROGRESS
                )
                self._check_cancellation()
                try:
                    from qualify.team import QualifyTeam
                except ImportError as e:
                    pipeline_log(
                        f"import failure: QualifyTeam ({e}); cwd={os.getcwd()}",
                        stage="qualify",
                        team="PipelineRunner",
                        project_id=self.project_id,
                        level=logging.ERROR,
                    )
                    raise
                qualify_team = QualifyTeam(project_id=self.project_id)
                try:
                    qualify_counts = await qualify_team.run()
                finally:
                    await qualify_team.cleanup()

                # fail-safe: when qualify COULDN'T RUN (health-check
                # failure → run() returns None), record the qualify STAGE as
                # error but do NOT fail the project and do NOT raise —
                # find/analyze/download already succeeded and qualify is a
                # bolt-on. The completion block below still runs the project to
                # "completed". Per-dataset "error" verdicts among successful
                # strict/loose/fail verdicts COMPLETE the stage — matching the
                # CLI path (orchestrator.py).
                qualify_unavailable = qualify_counts is None
                if qualify_unavailable:
                    self.dao.complete_stage(
                        stage_id, status="failed",
                        error_message="Qualify stage could not run (claude unavailable)",
                    )
                    await self.websocket_handler.update_project_stage(
                        self.project_id, PipelineStage.QUALIFY, StageStatus.ERROR
                    )
                    pipeline_log(
                        "qualify stage unavailable (claude not installed/authed); "
                        "recording stage error but completing the project — "
                        f"qualify={qualify_counts}",
                        stage="qualify",
                        team="PipelineRunner",
                        project_id=self.project_id,
                        level=logging.ERROR,
                    )
                else:
                    await self.websocket_handler.update_project_stage(
                        self.project_id, PipelineStage.QUALIFY, StageStatus.COMPLETED, 1.0
                    )
                    self.dao.complete_stage(stage_id, status="completed")
                    self._log_execution(
                        event_type="stage_complete",
                        message=f"Qualify stage completed: {qualify_counts}",
                        stage_name="qualify",
                        payload={"qualify_counts": qualify_counts or {}},
                    )
            else:
                await self.websocket_handler.update_project_stage(
                    self.project_id, PipelineStage.QUALIFY, StageStatus.COMPLETED, 1.0
                )

            pipeline_log(
                "pipeline complete: "
                f"downloads_collected={len(downloads)} (run return values), "
                f"dataset_artifacts={total_dataset_artifact_count}; qualify={qualify_counts}",
                stage="pipeline",
                team="PipelineRunner",
                project_id=self.project_id,
            )
            await self.ui.update_stage("Pipeline - Complete")
            await self.ui.update_status(
                f"Pipeline complete! Downloaded {total_dataset_artifact_count} datasets",
                "system",
            )

            self.dao.update_project_status(self.project_id, "completed")
            self._log_execution(
                event_type="pipeline_complete",
                message="Pipeline completed successfully",
                stage_name="pipeline",
                payload={
                    "paper_count": len(papers),
                    "analysis_count": len(analyses),
                    "download_count": total_dataset_artifact_count,
                },
            )
            pipeline_log(
                "project status updated in database: completed",
                stage="pipeline",
                team="PipelineRunner",
                project_id=self.project_id,
            )

            await self.websocket_handler.update_project_stage(
                self.project_id, PipelineStage.COMPLETE, StageStatus.COMPLETED, 1.0
            )

            self._log_message(
                "pipeline",
                pm(
                    "pipeline.completed",
                    papers=len(papers),
                    analyses=len(analyses),
                    datasets=total_dataset_artifact_count,
                ),
                "info"
            )
        except Exception as e:
            self._log_message("pipeline", pm("pipeline.failed", error=e), "error")
            self._log_execution(
                event_type="pipeline_failed",
                message=f"Pipeline failed: {e}",
                severity="error",
                stage_name="pipeline",
            )
            pipeline_log(
                f"pipeline failed: {e}",
                stage="pipeline",
                team="PipelineRunner",
                project_id=self.project_id,
                level=logging.ERROR,
            )
            logger.exception("Pipeline failed")

            if 'stage_id' in locals():
                try:
                    self.dao.complete_stage(stage_id, status='failed', error_message=str(e))
                    pipeline_log(
                        f"marked stage row as failed in database stage_id={stage_id}",
                        stage="pipeline",
                        team="PipelineRunner",
                        project_id=self.project_id,
                    )
                except Exception as db_error:
                    pipeline_log(
                        f"failed to mark stage row as failed: {db_error}",
                        stage="pipeline",
                        team="PipelineRunner",
                        project_id=self.project_id,
                        level=logging.WARNING,
                    )

            await self.ui.update_status(f"Pipeline failed: {e}", "error")

            current_stage = self._get_current_pipeline_stage()
            if current_stage:
                await self.websocket_handler.update_project_stage(
                    self.project_id, current_stage, StageStatus.ERROR
                )

            raise
        finally:
            await self.cleanup()

    def _get_current_pipeline_stage(self) -> Optional[PipelineStage]:
        return self.ui._parse_stage_name(self.ui._current_stage)

    def _log_execution(
        self,
        event_type: str,
        message: str,
        severity: str = "info",
        stage_name: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        try:
            self.dao.add_execution_log(
                project_id=self.project_id,
                event_type=event_type,
                message=message,
                severity=severity,
                stage_name=stage_name,
                team_name="PipelineRunner",
                payload=payload or {},
            )
        except Exception as e:
            pipeline_log(
                f"db execution_log insert failed event={event_type}: {e}",
                stage=stage_name or "pipeline",
                team="PipelineRunner",
                project_id=self.project_id,
                level=logging.WARNING,
            )

    def _log_message(self, stage_name: str, content: str, message_type: str = 'info'):
        try:
            self.dao.add_message(
                project_id=self.project_id,
                stage_name=stage_name,
                team_name="orchestrator",
                content=content,
                message_type=message_type
            )
        except Exception as e:
            pipeline_log(
                f"db message insert failed stage={stage_name}: {e}",
                stage=stage_name,
                team="PipelineRunner",
                project_id=self.project_id,
                level=logging.WARNING,
            )

    async def cleanup(self):
        pipeline_log(
            "cleanup: releasing team resources",
            stage="pipeline",
            team="PipelineRunner",
            project_id=self.project_id,
        )
        await self.ui.update_status("Cleaning up resources...", "system")

        for team in [self.find_team, self.analysis_team, self.download_team]:
            if team:
                try:
                    await team.cleanup()
                except Exception as e:
                    pipeline_log(
                        f"team cleanup warning team={getattr(team, 'team_name', type(team).__name__)} error={e}",
                        stage="pipeline",
                        team="PipelineRunner",
                        project_id=self.project_id,
                        level=logging.WARNING,
                    )

        self.ui.stop()

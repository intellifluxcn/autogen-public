"""Route acquisition plans across multiple specialized executors."""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from analyze.plan_model import AcquisitionTask, DownloadPlan
from database.dao import ProjectDAO
from download.acquisition_result import AcquisitionRunResult, AcquisitionTaskResult, ProducedArtifact
from download.author_contact_team import AuthorContactTeam
from download.direct_download import DirectDownloadTeam
from download.embedded_data_team import EmbeddedDataTeam
from download.plan_gate import (
    PlanGateDecision,
    classify_repository_task,
    embedded_replacement_task_for_repository,
    public_repository_task_for_mixed_access,
    repository_task_has_embedded_fallback_intent,
)
from download.team import WebsiteDownloadTeam
from utils.path_utils import get_browseruse_datasets_dir
from utils.pipeline_log import pipeline_log

logger = logging.getLogger(__name__)

TASK_ORDER = {
    "repository_download": 0,
    "embedded_extraction": 1,
    "author_contact": 2,
}

# Short-circuit guard: only stop running additional acquisition tasks when
# coverage is met AND we have at least this many evidence-bearing artifacts
# from successful tasks. Prevents an optimistic coverage flag on a single
# `partial`/`skipped` task from masking missing data. Override per-deploy
# via ROUTER_MIN_EVIDENCE_COUNT.
ROUTER_MIN_EVIDENCE_COUNT: int = max(
    1, int(os.getenv("ROUTER_MIN_EVIDENCE_COUNT", "1"))
)


def _count_successful_artifacts(task_results: List[AcquisitionTaskResult]) -> int:
    """Count produced artifacts from tasks that reported completed/partial."""
    count = 0
    for task_result in task_results:
        if task_result.status in ("completed", "partial"):
            count += len(task_result.produced_artifacts)
    return count


class AcquisitionRouter:
    """Dispatch acquisition plans to strategy-specific executors."""

    def __init__(self, ui_callback=None, team_name: str = "AcquisitionRouter", project_id: str = None,
                 cancellation_check=None, download_model_override: Optional[str] = None):
        self.ui_callback = ui_callback
        self.team_name = team_name
        self.project_id = project_id
        self.download_model_override = download_model_override
        self.dao = ProjectDAO() if project_id else None
        self.website_team = WebsiteDownloadTeam(
            ui_callback=ui_callback,
            team_name="WebsiteDownloadTeam",
            project_id=project_id,
            cancellation_check=cancellation_check,
            download_model_override=download_model_override,
        )
        self.direct_team = DirectDownloadTeam(
            ui_callback=ui_callback,
            team_name="DirectDownloadTeam",
            project_id=project_id,
        )
        self.author_team = AuthorContactTeam(
            ui_callback=ui_callback,
            team_name="AuthorContactTeam",
            project_id=project_id,
        )
        self.embedded_team = EmbeddedDataTeam(
            ui_callback=ui_callback,
            team_name="EmbeddedDataTeam",
            project_id=project_id,
        )

    @staticmethod
    def _sort_tasks(tasks: List[AcquisitionTask]) -> List[AcquisitionTask]:
        return sorted(
            tasks,
            key=lambda task: (TASK_ORDER.get(task.task_type, 99), task.priority, task.title or ""),
        )

    def _persist_non_dataset_artifacts(self, task_result: AcquisitionTaskResult) -> None:
        if not self.dao or not self.project_id:
            return
        for artifact in task_result.produced_artifacts:
            if artifact.artifact_type == "dataset":
                continue
            try:
                # embedded_dataset files (PDF-extracted tables + downloaded
                # supplements) are real data the qualify stage must read. Mirror
                # the `dataset` path: upload to remote storage + record the key so
                # qualify's OSS fallback can re-materialize them and local cleanup
                # never loses them. acquisition_evidence (small JSON reports) stay
                # local-only. Best-effort: a failed upload records local-only.
                storage_kwargs = {}
                if artifact.artifact_type == "embedded_dataset":
                    storage_kwargs = self._upload_embedded_to_storage(artifact)
                self.dao.add_artifact(
                    project_id=self.project_id,
                    artifact_type=artifact.artifact_type,
                    file_path=artifact.file_path,
                    stage_name="download",
                    acquisition_source=artifact.acquisition_source,
                    acquisition_status=artifact.acquisition_status,
                    trust_level=artifact.trust_level,
                    confidence=artifact.confidence,
                    produced_by=artifact.produced_by,
                    provenance=artifact.provenance,
                    **storage_kwargs,
                )
            except Exception as e:
                pipeline_log(
                    f"artifact persist failed path={artifact.file_path!r}: {e}",
                    stage="download",
                    team=self.team_name,
                    project_id=self.project_id,
                    level=logging.WARNING,
                )

    def _upload_embedded_to_storage(self, artifact: ProducedArtifact) -> Dict[str, Any]:
        """Upload one embedded_dataset file to remote storage and return the
        add_artifact storage_metadata kwarg (empty on failure / local backend)."""
        from download.artifact_finalize import (
            _maybe_remove_local_after_upload,
            _upload_with_retries,
        )
        from storage import get_storage

        paper_name = os.path.basename(os.path.dirname(artifact.file_path))
        key = f"datasets/{paper_name}/{os.path.basename(artifact.file_path)}"
        try:
            result = _upload_with_retries(
                storage=get_storage(),
                local_path=artifact.file_path,
                key=key,
                team_name=self.team_name,
                project_id=self.project_id,
            )
        except Exception as e:
            pipeline_log(
                f"embedded upload failed, recording local-only: {artifact.file_path!r} ({e})",
                stage="download", team=self.team_name,
                project_id=self.project_id, level=logging.WARNING,
            )
            return {}
        pipeline_log(
            f"Uploaded embedded dataset artifact: {key}",
            stage="download", team=self.team_name, project_id=self.project_id,
        )
        _maybe_remove_local_after_upload(
            artifact.file_path, result, team_name=self.team_name, project_id=self.project_id,
        )
        return {
            "storage_metadata": {
                "storage_backend": result.storage_backend,
                "s3_bucket": result.s3_bucket,
                "s3_key": result.s3_key,
                "oss_bucket": result.oss_bucket,
                "oss_key": result.oss_key,
            }
        }

    def _write_gate_evidence_artifact(
        self,
        *,
        task: AcquisitionTask,
        decision: PlanGateDecision,
        paper_name: str,
    ) -> ProducedArtifact:
        output_dir = Path(get_browseruse_datasets_dir()) / paper_name
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / "acquisition_blocked_report.json"
        report = {
            "paper": paper_name,
            "task_type": task.task_type,
            "route_category": decision.action,
            "route_reason": decision.reason,
            "matched_signals": list(decision.matched_signals),
            "has_actionable_url": decision.has_actionable_url,
            "blocking_gaps": list(task.blocking_gaps),
            "steps": [step.model_dump() for step in task.steps],
        }
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        source = (
            "controlled_access"
            if decision.action in {"blocked_by_auth", "blocked_by_approval"}
            else "repository_evidence"
        )
        return ProducedArtifact(
            file_path=str(report_path),
            artifact_type="acquisition_evidence",
            acquisition_source=source,
            acquisition_status=decision.action,
            trust_level="low",
            confidence=0.95,
            produced_by=self.__class__.__name__,
            provenance={
                "paper": paper_name,
                "route_category": decision.action,
                "route_reason": decision.reason,
                "matched_signals": list(decision.matched_signals),
                "has_actionable_url": decision.has_actionable_url,
                "blocking_gaps": list(task.blocking_gaps),
                "report_type": "acquisition_blocked_report",
            },
        )

    def _gate_skip_result(
        self,
        *,
        task: AcquisitionTask,
        decision: PlanGateDecision,
        paper_name: str,
    ) -> Dict[str, Any]:
        pipeline_log(
            f"plan gate skipped repository task action={decision.action!r} "
            f"paper={paper_name!r} reason={decision.reason}",
            stage="download",
            team=self.team_name,
            project_id=self.project_id,
            level=logging.WARNING,
        )
        evidence = self._write_gate_evidence_artifact(
            task=task,
            decision=decision,
            paper_name=paper_name,
        )
        return AcquisitionTaskResult(
            task_type=task.task_type,
            status="skipped",
            produced_artifacts=[evidence],
            remaining_gaps=list(dict.fromkeys([decision.action] + list(task.blocking_gaps))),
            message=decision.reason,
            metadata={
                "paper": paper_name,
                "route_category": decision.action,
                "route_reason": decision.reason,
                "matched_signals": list(decision.matched_signals),
                "has_actionable_url": decision.has_actionable_url,
            },
        ).model_dump()

    @staticmethod
    def _result_has_dataset_artifact(result: AcquisitionTaskResult) -> bool:
        return any(
            artifact.artifact_type in {"dataset", "embedded_dataset"}
            for artifact in result.produced_artifacts
        )

    def _cleanup_stale_no_valid_report(self, paper_name: str) -> None:
        """Remove stale no-valid evidence after a later fallback captured a dataset."""
        report_path = Path(get_browseruse_datasets_dir()) / paper_name / "no_valid_dataset_report.json"
        if report_path.exists():
            try:
                report_path.unlink()
                pipeline_log(
                    f"removed stale no-valid-dataset report after fallback success: {report_path}",
                    stage="download",
                    team=self.team_name,
                    project_id=self.project_id,
                )
            except OSError as e:
                pipeline_log(
                    f"failed to remove stale no-valid-dataset report {report_path}: {e}",
                    stage="download",
                    team=self.team_name,
                    project_id=self.project_id,
                    level=logging.WARNING,
                )

        if not self.dao or not self.project_id:
            return
        try:
            deleted_count = self.dao.delete_artifact_by_path(
                project_id=self.project_id,
                file_path=str(report_path),
            )
        except Exception as e:
            pipeline_log(
                f"failed to remove stale no-valid-dataset artifact record: {e}",
                stage="download",
                team=self.team_name,
                project_id=self.project_id,
                level=logging.WARNING,
            )
            return
        if deleted_count:
            pipeline_log(
                f"removed {deleted_count} stale no-valid-dataset artifact record(s)",
                stage="download",
                team=self.team_name,
                project_id=self.project_id,
            )

    async def _maybe_run_embedded_fallback(
        self,
        *,
        plan: DownloadPlan,
        task: AcquisitionTask,
        plan_path: str,
        paper_name: str,
        result_model: AcquisitionTaskResult,
    ) -> Optional[dict[str, Any]]:
        has_data = self._result_has_dataset_artifact(result_model)
        has_r = bool(result_model.coverage and result_model.coverage.drug_response_data)
        # Drug response (R) already acquired → nothing to rescue.
        if has_r:
            return None
        # Clinical requirement: R may live only in figures/prose, so a repository task
        # that fetched expression (E) but NO drug response must still try embedded
        # extraction (table + multimodal LLM figure/text). Trigger when R is
        # missing — whether we got E-only or nothing. For the "got nothing" case
        # keep the original intent gate to avoid blind embedded runs; for the
        # "got E, missing R" case always try (R is the whole point).
        if not has_data and not repository_task_has_embedded_fallback_intent(plan, task):
            return None

        pipeline_log(
            f"repository task missing drug-response (has_data={has_data}); trying embedded "
            f"R-extraction fallback paper={paper_name!r}",
            stage="download",
            team=self.team_name,
            project_id=self.project_id,
        )
        fallback_task = embedded_replacement_task_for_repository(plan, task)
        # Guarantee the multimodal R extraction fires (it keys on a figure/page
        # target) even when unrelated tables exist in the PDF: add an explicit
        # drug-response figure target since we know R is still missing here.
        if not any(
            t.target_type in ("figure", "page") for t in (fallback_task.embedded_targets or [])
        ):
            from analyze.plan_model import EmbeddedTarget
            targets = list(fallback_task.embedded_targets or [])
            targets.append(
                EmbeddedTarget(
                    target_type="figure",
                    label="drug response",
                    description=(
                        "Drug-response (IC50/AUC/GI50/viability) values that may appear "
                        "only in figures (dose-response curves, bar charts, heatmaps) or prose"
                    ),
                )
            )
            fallback_task = fallback_task.model_copy(update={"embedded_targets": targets})
        raw_result = await self.embedded_team.run(
            plan=plan,
            task=fallback_task,
            plan_path=plan_path,
        )
        raw_result.setdefault("metadata", {})
        raw_result["metadata"].update(
            {
                "route_category": "repository_embedded_fallback",
                "route_reason": "Repository/browser download produced no valid dataset files",
            }
        )
        return raw_result

    async def run(self, plan_path: str = None, plan_data: Dict[str, Any] = None) -> Dict[str, Any]:
        if plan_data and plan_data.get("plan_path") and not plan_path:
            plan_path = plan_data["plan_path"]
        if not plan_path:
            raise ValueError("AcquisitionRouter requires plan_path")

        plan = self.website_team._load_download_plan(plan_path)
        paper_name = Path(plan_path).stem.replace(".plan", "")
        pipeline_log(
            f"routing acquisition plan primary_strategy={plan.primary_strategy!r} tasks={len(plan.tasks)} "
            f"paper={paper_name!r}",
            stage="download",
            team=self.team_name,
            project_id=self.project_id,
        )

        task_results: List[AcquisitionTaskResult] = []
        for task in self._sort_tasks(plan.tasks):
            if task.task_type == "repository_download":
                decision = classify_repository_task(plan, task)
                if decision.action == "allow_repository_download":
                    executable_task = public_repository_task_for_mixed_access(task)
                    if executable_task is not task:
                        pipeline_log(
                            f"plan gate retained public repository steps only "
                            f"paper={paper_name!r} original_steps={len(task.steps)} "
                            f"public_steps={len(executable_task.steps)}",
                            stage="download",
                            team=self.team_name,
                            project_id=self.project_id,
                        )
                    raw_result = await self.direct_team.run(
                        plan=plan,
                        task=executable_task,
                        plan_path=plan_path,
                        status_helper=self.website_team.status_reporter,
                        build_downloaded_artifact_descriptor=(
                            self.website_team._build_downloaded_artifact_descriptor
                        ),
                        artifact_source_for_path=self.website_team._artifact_source_for_path,
                        wait_for_downloads_to_complete=self.website_team._wait_for_downloads_to_complete,
                    )
                    result_model = (
                        AcquisitionTaskResult.model_validate(raw_result)
                        if raw_result is not None
                        else None
                    )
                    if (
                        result_model is None
                        or result_model.status == "skipped"
                        and not result_model.produced_artifacts
                    ):
                        raw_result = await self.website_team.run(
                            plan_path=plan_path,
                            task_override=executable_task,
                            loaded_plan=plan,
                        )
                    result_model = AcquisitionTaskResult.model_validate(raw_result)
                    fallback_result = await self._maybe_run_embedded_fallback(
                        plan=plan,
                        task=executable_task,
                        plan_path=plan_path,
                        paper_name=paper_name,
                        result_model=result_model,
                    )
                    if fallback_result is not None:
                        task_results.append(result_model)
                        self._persist_non_dataset_artifacts(result_model)
                        fallback_model = AcquisitionTaskResult.model_validate(fallback_result)
                        if self._result_has_dataset_artifact(fallback_model):
                            self._cleanup_stale_no_valid_report(paper_name)
                        raw_result = fallback_result
                elif decision.action == "route_author_contact" and decision.replacement_task:
                    pipeline_log(
                        f"plan gate rerouted repository task to author_contact paper={paper_name!r}",
                        stage="download",
                        team=self.team_name,
                        project_id=self.project_id,
                    )
                    raw_result = await self.author_team.run(
                        plan=plan,
                        task=decision.replacement_task,
                        plan_path=plan_path,
                    )
                    raw_result.setdefault("metadata", {})
                    raw_result["metadata"].update(
                        {
                            "route_category": decision.action,
                            "route_reason": decision.reason,
                            "matched_signals": list(decision.matched_signals),
                            "has_actionable_url": decision.has_actionable_url,
                        }
                    )
                elif decision.action == "route_embedded_extraction" and decision.replacement_task:
                    pipeline_log(
                        f"plan gate rerouted repository task to embedded_extraction paper={paper_name!r}",
                        stage="download",
                        team=self.team_name,
                        project_id=self.project_id,
                    )
                    raw_result = await self.embedded_team.run(
                        plan=plan,
                        task=decision.replacement_task,
                        plan_path=plan_path,
                    )
                    raw_result.setdefault("metadata", {})
                    raw_result["metadata"].update(
                        {
                            "route_category": decision.action,
                            "route_reason": decision.reason,
                            "matched_signals": list(decision.matched_signals),
                            "has_actionable_url": decision.has_actionable_url,
                        }
                    )
                else:
                    raw_result = self._gate_skip_result(
                        task=task,
                        decision=decision,
                        paper_name=paper_name,
                    )
            elif task.task_type == "embedded_extraction":
                raw_result = await self.embedded_team.run(plan=plan, task=task, plan_path=plan_path)
            elif task.task_type == "author_contact":
                raw_result = await self.author_team.run(plan=plan, task=task, plan_path=plan_path)
            else:
                raw_result = AcquisitionTaskResult(
                    task_type=task.task_type,
                    status="skipped",
                    remaining_gaps=list(task.blocking_gaps),
                    message=f"Unsupported acquisition task type: {task.task_type}",
                ).model_dump()

            result_model = AcquisitionTaskResult.model_validate(raw_result)
            task_results.append(result_model)
            self._persist_non_dataset_artifacts(result_model)

            if result_model.coverage.sequencing_data and result_model.coverage.drug_response_data:
                evidence_count = _count_successful_artifacts(task_results)
                if evidence_count >= ROUTER_MIN_EVIDENCE_COUNT:
                    pipeline_log(
                        f"routing short-circuit after task_type={task.task_type!r} "
                        f"for paper={paper_name!r} (evidence_count={evidence_count} "
                        f">= {ROUTER_MIN_EVIDENCE_COUNT})",
                        stage="download",
                        team=self.team_name,
                        project_id=self.project_id,
                    )
                    break
                pipeline_log(
                    f"routing: coverage met for paper={paper_name!r} but evidence_count="
                    f"{evidence_count} < {ROUTER_MIN_EVIDENCE_COUNT}; continuing to "
                    f"remaining tasks to avoid premature short-circuit",
                    stage="download",
                    team=self.team_name,
                    project_id=self.project_id,
                    level=logging.WARNING,
                )

        aggregated = AcquisitionRunResult.from_task_results(
            primary_strategy=plan.primary_strategy,
            task_results=task_results,
            metadata={"paper": paper_name, "task_count": len(plan.tasks)},
        )
        return aggregated.model_dump()

    async def cleanup(self) -> None:
        await self.direct_team.cleanup()
        await self.website_team.cleanup()
        await self.author_team.cleanup()
        await self.embedded_team.cleanup()

"""Author-contact acquisition executor."""

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from analyze.plan_model import AcquisitionTask, ContactTarget, DownloadPlan
from database.author_parser import AuthorParser
from database.dao import ProjectDAO
from download.acquisition_result import AcquisitionTaskResult, CoverageSummary, ProducedArtifact
from utils.path_utils import get_browseruse_datasets_dir
from utils.pipeline_log import pipeline_log

logger = logging.getLogger(__name__)


class AuthorContactTeam:
    """Prepare author-contact email drafts for acquisition plans."""

    def __init__(self, team_name: str = "AuthorContactTeam", project_id: str = None, ui_callback=None):
        self.team_name = team_name
        self.project_id = project_id
        self.ui_callback = ui_callback
        self.dao: Optional[ProjectDAO] = ProjectDAO() if self.project_id else None
        self.output_dir = Path(get_browseruse_datasets_dir())
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _update_status(self, message: str) -> None:
        pipeline_log(message, stage="download", team=self.team_name, project_id=self.project_id)
        if self.ui_callback:
            self.ui_callback("status", message)

    @staticmethod
    def _notes_path_for_plan(plan_path: str) -> Path:
        return Path(plan_path).with_suffix("").with_suffix(".md")

    def _analysis_artifact_id_for_plan(self, plan_path: str) -> Optional[int]:
        if not self.dao or not self.project_id:
            return None
        notes_name = self._notes_path_for_plan(plan_path).name
        plan_stem = Path(plan_path).stem.replace(".plan", "")
        for artifact in self.dao.get_project_artifacts(self.project_id, "analysis"):
            artifact_path = str(artifact.get("file_path") or "")
            artifact_name = str(artifact.get("file_name") or Path(artifact_path).name)
            if artifact_name == notes_name or Path(artifact_path).stem == plan_stem:
                return artifact.get("id")
        return None

    def _load_author_info(self, task: AcquisitionTask, plan_path: str) -> Dict[str, Any]:
        contact_targets = [target.model_dump() for target in task.contact_targets]
        notes_path = self._notes_path_for_plan(plan_path)
        if notes_path.exists():
            file_content = notes_path.read_text(encoding="utf-8")
            parsed = AuthorParser.parse_author_info(file_content)
        else:
            parsed = {"title": "", "authors": [], "emails": []}

        if not contact_targets and (parsed.get("authors") or parsed.get("emails")):
            contact_targets = [
                ContactTarget(name=parsed["authors"][0] if parsed["authors"] else None, email=email).model_dump()
                for email in parsed.get("emails", [])
            ] or [
                ContactTarget(name=name).model_dump() for name in parsed.get("authors", [])
            ]

        return {"contact_targets": contact_targets, "parsed_author_info": parsed}

    async def run(
        self,
        *,
        plan: DownloadPlan,
        task: AcquisitionTask,
        plan_path: str,
    ) -> Dict[str, Any]:
        self._update_status("Preparing author contact task")
        info = self._load_author_info(task, plan_path)
        contact_targets = info["contact_targets"]
        recipients = [target.get("email") for target in contact_targets if target.get("email")]

        paper_name = Path(plan_path).stem.replace(".plan", "")
        artifact_id = self._analysis_artifact_id_for_plan(plan_path)

        status = "partial"
        remaining_gaps = list(task.blocking_gaps or plan.blocking_gaps)
        produced_artifacts: list[ProducedArtifact] = []
        metadata: Dict[str, Any] = {
            "contact_state": "pending_contact",
            "contact_targets": contact_targets,
            "analysis_artifact_id": artifact_id,
        }

        if recipients and self.dao and self.project_id:
            try:
                from web_ui.backend.email_service import generate_contact_email

                email = generate_contact_email(
                    dao=self.dao,
                    project_id=self.project_id,
                    artifact_id=artifact_id,
                )
                metadata["contact_state"] = "draft_ready"
                metadata["email_subject"] = email["subject"]
                remaining_gaps = list(dict.fromkeys(["author_contact_required"] + remaining_gaps))

                contact_report_path = self.output_dir / paper_name / "author_contact_report.json"
                contact_report_path.parent.mkdir(parents=True, exist_ok=True)
                contact_report_path.write_text(
                    json.dumps(
                        {
                            "paper": paper_name,
                            "recipients": recipients,
                            "contact_state": metadata["contact_state"],
                            "email_subject": email["subject"],
                            "plan_primary_strategy": plan.primary_strategy,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                produced_artifacts.append(
                    ProducedArtifact(
                        file_path=str(contact_report_path),
                        artifact_type="acquisition_evidence",
                        acquisition_source="contact_author",
                        acquisition_status="awaiting_external",
                        trust_level="low",
                        confidence=0.9,
                        produced_by=self.__class__.__name__,
                        provenance={
                            "paper": paper_name,
                            "recipients": recipients,
                            "contact_state": metadata["contact_state"],
                        },
                    )
                )
            except Exception as e:
                logger.exception("Author contact task failed")
                metadata["contact_state"] = "draft_failed"
                metadata["error"] = str(e)
                status = "failed"
        else:
            metadata["contact_state"] = "pending_contact"
            if not recipients:
                remaining_gaps = list(dict.fromkeys(["author_email_missing"] + remaining_gaps))

        self._update_status(f"Author contact task finished with state={metadata['contact_state']}")
        return AcquisitionTaskResult(
            task_type="author_contact",
            status=status,
            produced_artifacts=produced_artifacts,
            coverage=CoverageSummary(),
            confidence=0.9 if status == "completed" else 0.4,
            remaining_gaps=remaining_gaps,
            message=f"Author contact state: {metadata['contact_state']}",
            metadata=metadata,
        ).model_dump()

    async def cleanup(self) -> None:
        pipeline_log(
            f"{self.__class__.__name__} cleaned up",
            stage="download",
            team=self.team_name,
            project_id=self.project_id,
        )

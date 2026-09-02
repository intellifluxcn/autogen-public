"""WebSocket handler for real-time communication between frontend and backend."""

import asyncio
import logging
from typing import Dict, Optional
from datetime import datetime, timezone

import socketio

from datetime_serialize import datetime_to_api_iso
from utils.pipeline_log import pipeline_log

from models import (
    Project, InputRequest, WebSocketMessage, Message,
    PipelineStage, StageStatus, MessageType
)

logger = logging.getLogger(__name__)


class WebSocketHandler:

    def __init__(
        self,
        sio: socketio.AsyncServer,
        input_requests: Dict[str, InputRequest],
        dao=None
    ):
        self.sio = sio
        self.input_requests = input_requests
        self.dao = dao
        self._input_responses: Dict[str, asyncio.Event] = {}
        self._input_response_values: Dict[str, str] = {}

    def _compute_overall_progress(
        self,
        snapshot: Optional[Project],
        stage: Optional[PipelineStage] = None,
        stage_progress: Optional[float] = None,
        status: Optional[StageStatus] = None,
    ) -> float:
        """Compute overall project progress from find/analyze/download stages."""
        if status == StageStatus.COMPLETED and stage == PipelineStage.COMPLETE:
            return 1.0

        tracked_stages = (
            PipelineStage.FIND,
            PipelineStage.ANALYZE,
            PipelineStage.DOWNLOAD,
            PipelineStage.QUALIFY,
        )
        progresses = []

        for tracked_stage in tracked_stages:
            progress_value = 0.0
            if snapshot and tracked_stage in snapshot.stages:
                progress_value = snapshot.stages[tracked_stage].progress or 0.0
            if tracked_stage == stage and stage_progress is not None:
                progress_value = stage_progress
            progress_value = max(0.0, min(1.0, progress_value))
            progresses.append(progress_value)

        return sum(progresses) / len(progresses) if progresses else 0.0

    def _build_project_snapshot(self, project_id: str) -> Optional[Project]:
        if not self.dao:
            return None
        db_project = self.dao.get_project(project_id)
        if not db_project:
            return None

        # P3-3: shared with main.py via status_maps; pre-fix this dict
        # was a local copy and drifted when statuses were added.
        from status_maps import PROJECT_STATUS_MAP, STAGE_NAME_MAP, STAGE_STATUS_MAP

        project = Project(
            id=db_project["id"],
            name=db_project["name"],
            query=db_project["research_query"],
            user_email=db_project.get("user_email"),
            status=PROJECT_STATUS_MAP.get(db_project.get("status"), StageStatus.PENDING),
            overall_progress=db_project.get("overall_progress", 0.0),
            created_at=db_project.get("created_at"),
            updated_at=db_project.get("updated_at"),
        )

        for stage_record in self.dao.get_project_stages(project_id):
            pipeline_stage = STAGE_NAME_MAP.get(stage_record.get("stage_name"))
            if not pipeline_stage:
                continue
            stage_info = project.stages[pipeline_stage]
            stage_info.status = STAGE_STATUS_MAP.get(stage_record.get("status"), StageStatus.PENDING)
            progress = stage_record.get("progress", 0.0)
            if stage_record.get("status") == "completed" and progress == 0.0:
                progress = 1.0
            stage_info.progress = progress
            stage_info.start_time = stage_record.get("start_time")
            stage_info.end_time = stage_record.get("end_time")
            stage_info.error_message = stage_record.get("error_message")

        # Project.current_stage defaults to FIND in models.py — without this
        # alignment, every message routed by add_project_message() fallback
        # would land in the find card (see commit message). Mirror the
        # main.py:_sync_project_current_stage_from_stages logic so the
        # WebSocket snapshot reflects the actually-active stage.
        order = [
            PipelineStage.FIND,
            PipelineStage.ANALYZE,
            PipelineStage.DOWNLOAD,
            PipelineStage.QUALIFY,
        ]
        active = None
        for ps in order:
            info = project.stages.get(ps)
            if info and info.status in (StageStatus.IN_PROGRESS, StageStatus.PAUSED):
                active = ps
                break
        if active is None:
            # No in-progress stage. Fall through to error/cancelled, then completed.
            if project.status in (StageStatus.ERROR, StageStatus.CANCELLED):
                for ps in reversed(order):
                    info = project.stages.get(ps)
                    if info and info.status in (StageStatus.ERROR, StageStatus.CANCELLED):
                        active = ps
                        break
            elif project.status == StageStatus.COMPLETED:
                active = PipelineStage.COMPLETE
        if active is not None:
            project.current_stage = active

        return project

    def _get_owner_email(self, project_id: Optional[str]) -> Optional[str]:
        if not project_id or not self.dao:
            return None
        db_project = self.dao.get_project(project_id)
        if not db_project:
            return None
        return db_project.get("user_email")

    async def send_to_user(self, user_email: str, message: WebSocketMessage):
        try:
            await self.sio.emit(message.type, message.dict_with_iso_dates(), room=f"user_{user_email}")
            logger.debug(f"Sent message to user {user_email}: {message.type}")
        except Exception as e:
            pipeline_log(
                f"websocket send_to_user failed user={user_email} type={message.type}: {e}",
                stage="pipeline",
                component="websocket",
                project_id=message.project_id,
                level=logging.ERROR,
            )

    async def broadcast_message(self, message: WebSocketMessage):
        try:
            owner_email = self._get_owner_email(message.project_id)
            if owner_email:
                await self.send_to_user(owner_email, message)
                return
            await self.sio.emit(message.type, message.dict_with_iso_dates())
            logger.debug(f"Broadcasted message: {message.type}")
        except Exception as e:
            pipeline_log(
                f"websocket broadcast failed type={message.type}: {e}",
                stage="pipeline",
                component="websocket",
                project_id=message.project_id,
                level=logging.ERROR,
            )

    async def send_to_project(self, project_id: str, message: WebSocketMessage):
        try:
            room = f"project_{project_id}"
            await self.sio.emit(message.type, message.dict_with_iso_dates(), room=room)
            logger.debug(f"Sent message to project {project_id}: {message.type}")
        except Exception as e:
            pipeline_log(
                f"websocket send_to_project failed type={message.type}: {e}",
                stage="pipeline",
                component="websocket",
                project_id=project_id,
                level=logging.ERROR,
            )

    async def update_project_stage(
        self,
        project_id: str,
        stage: PipelineStage,
        status: StageStatus,
        progress: float = None
    ):
        if not self.dao:
            pipeline_log(
                "websocket stage update skipped: DAO not configured",
                stage=stage.value,
                component="websocket",
                project_id=project_id,
                level=logging.ERROR,
            )
            return

        from status_maps import STAGE_STATUS_TO_DB as status_db_map  # noqa: E402
        status_db = status_db_map.get(status, "pending")
        if status_db in {"running", "paused", "completed", "failed"}:
            stage_id = self.dao.get_or_start_stage(project_id, stage.value)
            if stage_id and progress is not None:
                self.dao.update_stage_progress(stage_id, progress)
            if stage_id and status_db in {"completed", "failed"}:
                self.dao.complete_stage(stage_id, status=status_db)
            if status_db == "paused":
                self.dao.pause_stage(project_id, stage.value)

        if status_db in {"running", "paused", "completed", "failed", "cancelled"}:
            self.dao.update_project_status(project_id, status_db)

        snapshot = self._build_project_snapshot(project_id)
        stage_progress = progress if progress is not None else (
            snapshot.stages[stage].progress if snapshot and stage in snapshot.stages else 0.0
        )
        overall_progress = self._compute_overall_progress(
            snapshot=snapshot,
            stage=stage,
            stage_progress=stage_progress,
            status=status,
        )
        self.dao.update_project_overall_progress(project_id, overall_progress)
        if snapshot:
            snapshot.overall_progress = overall_progress

        await self.broadcast_message(WebSocketMessage(
            type="stage_updated",
            project_id=project_id,
            data={
                "stage": stage.value,
                "status": status.value,
                "progress": stage_progress,
                "overall_progress": overall_progress
            }
        ))

        if snapshot:
            if snapshot.status == StageStatus.CANCELLED:
                await self.broadcast_message(WebSocketMessage(
                    type="project_deleted",
                    project_id=project_id,
                    data={}
                ))
            else:
                await self.broadcast_message(WebSocketMessage(
                    type="project_updated",
                    project_id=project_id,
                    data=snapshot.dict_with_iso_dates()
                ))

    async def add_project_message(
        self,
        project_id: str,
        content: str,
        message_type: MessageType = MessageType.SYSTEM,
        team_name: Optional[str] = None,
        stage: Optional[PipelineStage] = None,
        persist: bool = True,
    ):
        message = Message(
            content=content,
            message_type=message_type,
            team_name=team_name
        )
        snapshot = self._build_project_snapshot(project_id)
        target_stage = stage or (snapshot.current_stage if snapshot else PipelineStage.FIND)
        if persist and self.dao:
            self.dao.add_message(
                project_id=project_id,
                content=content,
                team_name=team_name,
                stage_name=target_stage.value,
                message_type=message_type.value,
            )

        await self.broadcast_message(WebSocketMessage(
            type="message_added",
            project_id=project_id,
            data={
                "message": message.dict_with_iso_dates(),
                "stage": target_stage.value
            }
        ))

    async def update_project_progress(
        self,
        project_id: str,
        stage: PipelineStage,
        progress: float
    ):
        if not self.dao:
            pipeline_log(
                "websocket progress update skipped: DAO not configured",
                stage=stage.value,
                component="websocket",
                project_id=project_id,
                level=logging.ERROR,
            )
            return

        try:
            self.dao.update_stage_progress_by_name(project_id, stage.value, progress)
        except Exception as e:
            pipeline_log(
                f"persist stage progress failed stage={stage.value}: {e}",
                stage=stage.value,
                component="websocket",
                project_id=project_id,
                level=logging.ERROR,
            )

        snapshot = self._build_project_snapshot(project_id)
        overall_progress = self._compute_overall_progress(
            snapshot=snapshot,
            stage=stage,
            stage_progress=progress,
        )
        self.dao.update_project_overall_progress(project_id, overall_progress)

        await self.broadcast_message(WebSocketMessage(
            type="progress_updated",
            project_id=project_id,
            data={
                "stage": stage.value,
                "progress": progress,
                "overall_progress": overall_progress
            }
        ))

    async def update_thinking(
        self,
        project_id: str,
        thinking: str,
        agent_name: Optional[str] = None
    ):
        if not self.dao or not self.dao.get_project(project_id):
            pipeline_log(
                "websocket update_thinking skipped: project not found",
                stage="pipeline",
                component="websocket",
                project_id=project_id,
                level=logging.ERROR,
            )
            return

        await self.broadcast_message(WebSocketMessage(
            type="thinking_updated",
            project_id=project_id,
            data={
                "thinking": thinking,
                "agent_name": agent_name,
                "timestamp": datetime_to_api_iso(datetime.now(timezone.utc)),
            }
        ))

    async def request_user_input(
        self,
        project_id: str,
        prompt: str,
        team_name: Optional[str] = None
    ) -> str:
        input_request = InputRequest(
            project_id=project_id,
            prompt=prompt,
            team_name=team_name
        )

        self.input_requests[input_request.id] = input_request

        response_event = asyncio.Event()
        self._input_responses[input_request.id] = response_event

        await self.broadcast_message(WebSocketMessage(
            type="input_requested",
            project_id=project_id,
            data={
                "input_id": input_request.id,
                "prompt": prompt,
                "team_name": team_name
            }
        ))

        await response_event.wait()

        response = self._input_response_values.get(input_request.id, "")

        if input_request.id in self._input_responses:
            del self._input_responses[input_request.id]
        if input_request.id in self._input_response_values:
            del self._input_response_values[input_request.id]

        return response

    async def handle_input_response(self, input_id: str, response: str):
        if input_id in self._input_responses:
            self._input_response_values[input_id] = response
            self._input_responses[input_id].set()

            input_request = self.input_requests.get(input_id)
            if input_request:
                await self.broadcast_message(WebSocketMessage(
                    type="input_response",
                    project_id=input_request.project_id,
                    data={
                        "input_id": input_id,
                        "response": response
                    }
                ))

    async def update_project_status(
        self,
        project_id: str,
        status: str,
        status_type: str = "system"
    ):
        await self.broadcast_message(WebSocketMessage(
            type="status_updated",
            project_id=project_id,
            data={
                "status": status,
                "status_type": status_type
            }
        ))


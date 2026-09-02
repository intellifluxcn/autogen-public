"""Shared maps from DB string statuses → typed enums.

Pre-fix these maps were duplicated three times: once in main.py
(PROJECT_STATUS_MAP, STAGE_STATUS_MAP, STAGE_NAME_MAP) and twice
inside websocket_handler.py (status_map / stage_status_map). When
the cancelled status was added the duplication bit — we caught it in
main.py but missed websocket_handler for one revision and the WS
broadcast briefly emitted PENDING for cancelled projects.

Hoisting them here means there's exactly one place to update going
forward (P3-3).
"""

from __future__ import annotations

from typing import Dict

from models import PipelineStage, StageStatus

# DB project.status (TEXT) → frontend StageStatus enum.
PROJECT_STATUS_MAP: Dict[str, StageStatus] = {
    "running": StageStatus.IN_PROGRESS,
    "paused": StageStatus.PAUSED,
    "completed": StageStatus.COMPLETED,
    "failed": StageStatus.ERROR,
    "cancelled": StageStatus.CANCELLED,
}

# DB stages.status (TEXT) → frontend StageStatus enum. Identical to
# PROJECT_STATUS_MAP today; kept as a separate symbol so we can
# diverge cleanly if stage-specific values appear (e.g. "skipped").
STAGE_STATUS_MAP: Dict[str, StageStatus] = dict(PROJECT_STATUS_MAP)

# DB stages.stage_name (TEXT) → typed PipelineStage enum.
STAGE_NAME_MAP: Dict[str, PipelineStage] = {
    "find": PipelineStage.FIND,
    "analyze": PipelineStage.ANALYZE,
    "download": PipelineStage.DOWNLOAD,
    "qualify": PipelineStage.QUALIFY,
}

# Reverse direction: PipelineStage / StageStatus → canonical DB string.
STAGE_NAME_TO_DB: Dict[PipelineStage, str] = {v: k for k, v in STAGE_NAME_MAP.items()}
STAGE_STATUS_TO_DB: Dict[StageStatus, str] = {
    StageStatus.IN_PROGRESS: "running",
    StageStatus.PAUSED: "paused",
    StageStatus.COMPLETED: "completed",
    StageStatus.ERROR: "failed",
    StageStatus.CANCELLED: "cancelled",
    StageStatus.PENDING: "pending",
}

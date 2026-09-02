"""
Structured pipeline logging for server logs (journal / stdout).

Format: [pipeline][stage=...][team=...][project=...] message
Use stage=find|analyze|download|pipeline; omit missing parts.
"""

import logging
from typing import Optional

logger = logging.getLogger("ai4med.pipeline")


def pipeline_log(
    message: str,
    *,
    stage: Optional[str] = None,
    team: Optional[str] = None,
    component: Optional[str] = None,
    project_id: Optional[str] = None,
    level: int = logging.INFO,
) -> None:
    """Emit one pipeline log line with a uniform prefix."""
    parts = ["pipeline"]
    if stage:
        parts.append(f"stage={stage}")
    if team:
        parts.append(f"team={team}")
    if component:
        parts.append(f"component={component}")
    if project_id:
        shown = project_id if len(project_id) <= 20 else (project_id[:12] + "…")
        parts.append(f"project={shown}")
    prefix = "".join(f"[{p}]" for p in parts)
    logger.log(level, "%s %s", prefix, message)

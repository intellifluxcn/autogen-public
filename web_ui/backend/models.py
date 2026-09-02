"""Data models for the AutoGen Web UI backend."""

from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic.json import custom_pydantic_encoder
from enum import Enum
import uuid
from datetime import datetime
import json

from datetime_serialize import datetime_to_api_iso


class StageStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ERROR = "error"
    CANCELLED = "cancelled"
    PAUSED = "paused"


class MessageType(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    TEAM = "team"


class PipelineStage(str, Enum):
    FIND = "find"
    ANALYZE = "analyze"
    DOWNLOAD = "download"
    QUALIFY = "qualify"
    COMPLETE = "complete"


class Message(BaseModel):
    id: str = None
    content: str
    message_type: MessageType = MessageType.SYSTEM
    team_name: Optional[str] = None
    timestamp: datetime = None

    def __init__(self, **data):
        if data.get('id') is None:
            data['id'] = str(uuid.uuid4())
        if data.get('timestamp') is None:
            data['timestamp'] = datetime.now()
        super().__init__(**data)

    def dict_with_iso_dates(self, **kwargs):
        data = self.model_dump(**kwargs)
        return self._convert_datetimes_to_iso(data)

    def _convert_datetimes_to_iso(self, data):
        if isinstance(data, datetime):
            return datetime_to_api_iso(data)
        elif hasattr(data, 'value'):
            return data.value
        elif hasattr(data, 'dict_with_iso_dates'):
            return data.dict_with_iso_dates()
        elif isinstance(data, dict):
            return {
                (k.value if hasattr(k, 'value') else k): self._convert_datetimes_to_iso(v)
                for k, v in data.items()
            }
        elif isinstance(data, list):
            return [self._convert_datetimes_to_iso(item) for item in data]
        return data


class StageInfo(BaseModel):
    stage: PipelineStage
    status: StageStatus = StageStatus.PENDING
    progress: float = 0.0
    artifact_count: int = 0
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    messages: List[Message] = []
    output_data: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None

    def dict_with_iso_dates(self, **kwargs):
        data = self.model_dump(**kwargs)
        return self._convert_datetimes_to_iso(data)

    def _convert_datetimes_to_iso(self, data):
        if isinstance(data, datetime):
            return datetime_to_api_iso(data)
        elif hasattr(data, 'value'):
            return data.value
        elif hasattr(data, 'dict_with_iso_dates'):
            return data.dict_with_iso_dates()
        elif isinstance(data, dict):
            return {
                (k.value if hasattr(k, 'value') else k): self._convert_datetimes_to_iso(v)
                for k, v in data.items()
            }
        elif isinstance(data, list):
            return [self._convert_datetimes_to_iso(item) for item in data]
        return data


class Project(BaseModel):
    id: str = None
    name: str
    query: str
    user_email: Optional[str] = None
    parallel_pipeline: bool = False
    analysis_model: Optional[str] = None
    download_model: Optional[str] = None
    created_at: datetime = None
    updated_at: datetime = None
    status: StageStatus = StageStatus.PENDING
    current_stage: PipelineStage = PipelineStage.FIND
    stages: Dict[PipelineStage, StageInfo] = {}
    overall_progress: float = 0.0

    def __init__(self, **data):
        if data.get('id') is None:
            data['id'] = str(uuid.uuid4())
        if data.get('created_at') is None:
            data['created_at'] = datetime.now()
        if data.get('updated_at') is None:
            data['updated_at'] = datetime.now()

        if not data.get('stages'):
            data['stages'] = {
                stage: StageInfo(stage=stage)
                for stage in PipelineStage
            }

        super().__init__(**data)

    def dict_with_iso_dates(self, **kwargs):
        data = self.model_dump(**kwargs)
        return self._convert_datetimes_to_iso(data)

    def _convert_datetimes_to_iso(self, data):
        if isinstance(data, datetime):
            return datetime_to_api_iso(data)
        elif hasattr(data, 'value'):
            return data.value
        elif hasattr(data, 'dict_with_iso_dates'):
            return data.dict_with_iso_dates()
        elif isinstance(data, dict):
            return {
                (k.value if hasattr(k, 'value') else k): self._convert_datetimes_to_iso(v)
                for k, v in data.items()
            }
        elif isinstance(data, list):
            return [self._convert_datetimes_to_iso(item) for item in data]
        return data


class InputRequest(BaseModel):
    id: str = None
    project_id: str
    prompt: str
    team_name: Optional[str] = None
    timestamp: datetime = None
    response: Optional[str] = None
    responded_at: Optional[datetime] = None

    def __init__(self, **data):
        if data.get('id') is None:
            data['id'] = str(uuid.uuid4())
        if data.get('timestamp') is None:
            data['timestamp'] = datetime.now()
        super().__init__(**data)


class WebSocketMessage(BaseModel):
    type: Literal[
        "project_created", "project_updated", "project_deleted",
        "project_status_changed",
        "stage_updated", "progress_updated", "message_added",
        "input_requested", "input_response", "status_updated"
    ]
    project_id: Optional[str] = None
    data: Dict[str, Any] = {}

    def dict_with_iso_dates(self, **kwargs):
        data = self.model_dump(**kwargs)
        return self._convert_datetimes_to_iso(data)

    def _convert_datetimes_to_iso(self, data):
        if isinstance(data, datetime):
            return datetime_to_api_iso(data)
        elif hasattr(data, 'value'):
            return data.value
        elif hasattr(data, 'dict_with_iso_dates'):
            return data.dict_with_iso_dates()
        elif isinstance(data, dict):
            return {
                (k.value if hasattr(k, 'value') else k): self._convert_datetimes_to_iso(v)
                for k, v in data.items()
            }
        elif isinstance(data, list):
            return [self._convert_datetimes_to_iso(item) for item in data]
        return data


class ProjectListResponse(BaseModel):
    items: List[Project]
    total: int
    page: int
    page_size: int


class ProjectDashboardStats(BaseModel):
    pending: int
    running: int
    paused: int
    completed: int
    failed: int
    active: int
    in_progress: int


def _strip_and_clean(value: str) -> str:
    """Trim whitespace and reject control characters (except common whitespace)."""
    cleaned = value.strip()
    if any(ord(c) < 0x20 and c not in ("\t", "\n", "\r") for c in cleaned):
        raise ValueError("Field contains disallowed control characters")
    return cleaned


class ReviewStatusUpdate(BaseModel):
    """payload for PATCH /api/artifacts/{id}/review-status.

    Allowed target statuses are constrained at the Literal level so the
    Pydantic validator rejects unknown values at request time. State
    machine transition rules (which source status → target is allowed) are
    enforced at the DAO layer via `only_from_statuses`.

    NOTE (Re-review LOW): ``supplemental_note`` is currently observability-only.
    The endpoint logs it via pipeline_log but does NOT persist it to the
    database. Documented in the field description so clients aren't misled
    by silent drop. Future work will write it to artifacts.provenance or
    analysis_cache.
    """
    status: Literal["processing", "handled", "skipped"]
    supplemental_note: Optional[str] = Field(
        default=None,
        max_length=5000,
        description="OBSERVABILITY-ONLY in current release — logged via pipeline_log but not persisted to the database.",
    )


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    query: str = Field(min_length=3, max_length=2000)
    # max_papers upper bound raised to 5000 for large-scale literature scans.
    # Note: NCBI ESearch single-call retmax caps at 10000, so candidate_pool
    # = max_papers × 3 starts hitting the cap above ~3333. _fill_to_target_pubmed
    # currently treats len(batch) < retmax as "query exhausted" and stops
    # paginating — so above ~3333 the effective candidate count plateaus
    # at ~10000 (≈ ~3000-5000 successful downloads at OA-only success
    # rate, or 5000+ with SciHub). Lifting beyond 5000 requires changing
    # _fill_to_target_pubmed to chunk ESearch at PAGE_SIZE=10000.
    max_papers: Optional[int] = Field(default=10, ge=1, le=5000)
    parallel_pipeline: bool = False
    analysis_model: Optional[str] = Field(default=None, max_length=200)
    download_model: Optional[str] = Field(default=None, max_length=200)
    # publication date range, optional. Format YYYY/MM/DD to
    # match FindTeam.run() defaults. None means "use Python-level defaults".
    date_start: Optional[str] = Field(
        default=None,
        pattern=r"^\d{4}/\d{2}/\d{2}$",
        description="Publication date lower bound (YYYY/MM/DD)",
    )
    date_end: Optional[str] = Field(
        default=None,
        pattern=r"^\d{4}/\d{2}/\d{2}$",
        description="Publication date upper bound (YYYY/MM/DD)",
    )
    # B4 escape hatch — bypass analysis_cache for this project.
    # Default False so existing project creation keeps cached-fast behavior.
    force_reanalyze: bool = Field(
        default=False,
        description="When true, the analyze stage ignores cached results and "
                    "re-runs the LLM for every paper. Useful when the prompt "
                    "template was recently bumped and the cache rows for the "
                    "old version should not be relied on.",
    )
    # MeSH synonym expansion in PubMed Find stage. Default True.
    mesh_expansion: bool = Field(
        default=True,
        description="When true, the Find stage's PubMed ESearch expands the "
                    "user query with NCBI MeSH synonyms before searching. "
                    "Automatically skipped when the user has already written "
                    "PubMed advanced syntax (field tags / uppercase boolean).",
    )

    @field_validator("name", "query")
    @classmethod
    def _clean_text(cls, v: str) -> str:
        cleaned = _strip_and_clean(v)
        if not cleaned:
            raise ValueError("Field cannot be blank or whitespace-only")
        return cleaned

    @model_validator(mode="after")
    def _validate_date_range(self) -> "CreateProjectRequest":
        """review fix: close the bypass gap where a direct API
        call could submit ``date_start > date_end``. NCBI ESearch silently
        returns an empty result for an inverted date range with no HTTP error,
        so we reject at the validator boundary instead."""
        if self.date_start and self.date_end and self.date_start > self.date_end:
            raise ValueError("date_start must be <= date_end (YYYY/MM/DD)")
        return self

    @field_validator("analysis_model", "download_model", mode="before")
    @classmethod
    def _clean_model_field(cls, v: Optional[str]) -> Optional[str]:
        """Strip whitespace; coerce empty/whitespace-only strings to None;
        validate non-null values against the allowed-model filter."""
        from utils.openrouter_filter import is_allowed_model
        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError("Model field must be a string")
        stripped = v.strip()
        if not stripped:
            return None
        if not is_allowed_model(stripped):
            raise ValueError(
                f"Model '{stripped}' is not allowed. "
                "Must be a google/, openai/, or anthropic/ model."
            )
        return stripped


class UpdateProjectRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    query: Optional[str] = Field(default=None, min_length=3, max_length=2000)

    @field_validator("name", "query")
    @classmethod
    def _clean_optional_text(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = _strip_and_clean(v)
        if not cleaned:
            raise ValueError("Field cannot be blank or whitespace-only")
        return cleaned


class InputResponse(BaseModel):
    input_id: str
    response: str


class ProjectControlAction(str, Enum):
    PAUSE = "pause"
    RESUME = "resume"
    CANCEL = "cancel"


class SendEmailRequest(BaseModel):
    to: List[str]
    subject: str
    body: str
    project_id: str
    artifact_id: Optional[int] = None


class GenerateEmailRequest(BaseModel):
    artifact_id: int
    project_id: str


class LinkedAccountCreate(BaseModel):
    site: str
    credential: str
    password: str


class LinkedAccountResponse(BaseModel):
    id: int
    site: str
    credential: str
    created_at: Optional[str] = None

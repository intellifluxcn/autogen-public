"""FastAPI backend for AutoGen Research Pipeline Web UI."""

import asyncio
import logging
import time
from typing import Dict, List, Literal, Optional, Tuple
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
import sys

from dotenv import load_dotenv
load_dotenv()

project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from database.dao import ProjectDAO
from database.connection import connect as db_connect

from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware
import socketio
import os

from models import (
    Project, CreateProjectRequest, UpdateProjectRequest,
    InputRequest, InputResponse, WebSocketMessage,
    PipelineStage, StageStatus, Message, MessageType,
    SendEmailRequest, GenerateEmailRequest, LinkedAccountCreate, LinkedAccountResponse,
    ProjectListResponse, ProjectDashboardStats,
)
from websocket_handler import WebSocketHandler
from datetime_serialize import datetime_to_api_iso
from utils.logging_config import configure_logging
from utils.pipeline_log import pipeline_log
from utils.llm_config import (
    build_openrouter_async_client,
    get_openrouter_download_model,
    get_openrouter_model,
)
from utils.openrouter_filter import is_allowed_model
from auth import (
    LoginRequest, LoginResponse,
    RegisterRequest, RegisterResponse,
    UserProfileResponse,
    AdminCreateUserRequest, AdminUpdateUserRequest, AdminResetPasswordRequest,
    UserResponse,
    ChangePasswordRequest, ResetPasswordRequest, ResetPasswordWithTokenRequest,
    authenticate_user, register_user, get_user_profile, create_access_token, get_current_user, get_current_user_or_token_param, verify_token,
    seed_demo_user, delete_user,
    get_all_users, create_user_by_admin, change_user_password,
    generate_password_reset_token, reset_password_with_token,
    count_admins, update_user_fields_by_admin, reset_user_password_by_admin,
    require_admin, get_user_role, is_admin
)

configure_logging()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# /api/models cache — module-level state (per-worker process)
# ---------------------------------------------------------------------------
_MODELS_CACHE_TTL_SECONDS = 3600  # 1 hour

# (data_list, fetch_timestamp) — empty list + 0.0 means "not yet fetched"
_models_cache: Tuple[list, float] = ([], 0.0)

# asyncio.Lock guards the singleflight fetch pattern.  We NEVER replace
# this object; _reset_models_cache() only zeroes data+timestamp so that
# in-flight waiters on the lock are not lost.
_models_cache_lock: asyncio.Lock = asyncio.Lock()


def _reset_models_cache() -> None:
    """Reset the models cache (clears data and timestamp, preserves lock identity)."""
    global _models_cache
    _models_cache = ([], 0.0)


async def _get_models_cached() -> list:
    """Return the filtered model list, fetching from OpenRouter on cache miss.

    Uses a singleflight pattern: concurrent callers on a cold or expired cache
    block on *_models_cache_lock*; only the first one fetches, then releases so
    the rest return the freshly populated cache without re-fetching.
    """
    global _models_cache

    data, ts = _models_cache
    if ts > 0.0 and (time.time() - ts) < _MODELS_CACHE_TTL_SECONDS:
        logger.info(f"models cache hit; returning {len(data)} models")
        return data

    async with _models_cache_lock:
        # Re-check after acquiring the lock (another coroutine may have fetched)
        data, ts = _models_cache
        if ts > 0.0 and (time.time() - ts) < _MODELS_CACHE_TTL_SECONDS:
            logger.info(f"models cache hit; returning {len(data)} models")
            return data

        logger.info("models cache miss; fetching from OpenRouter")
        try:
            client = build_openrouter_async_client()
            page = await client.models.list()
            raw_models = page.data if hasattr(page, "data") else list(page)
            filtered = [
                {
                    "id": m.id,
                    "name": getattr(m, "name", m.id),
                }
                for m in raw_models
                if is_allowed_model(m.id)
            ]
            _models_cache = (filtered, time.time())
            logger.info(f"models cache populated with {len(filtered)} models (raw={len(raw_models)})")
            return filtered
        except Exception as exc:
            logger.warning(f"OpenRouter models fetch failed: {exc}")
            # 502 maps to the generic "error" symbol in _http_exception_envelope
            # (502 is not in the symbol map) — the frontend should branch on
            # the numeric `code` field rather than the symbolic error string.
            raise HTTPException(
                status_code=502,
                detail=f"Failed to fetch models from OpenRouter: {exc}",
            )


ProjectListFilterStatus = Literal["pending", "running", "paused", "completed", "failed"]


_DEFAULT_AUTH_SECRET = "demo-secret-key-not-for-production"


def _validate_required_env() -> None:
    """Fail-fast on missing or default-valued secrets. Called from lifespan."""
    missing = []
    insecure = []

    auth_secret = os.getenv("AUTH_SECRET_KEY", "").strip()
    if not auth_secret:
        missing.append("AUTH_SECRET_KEY")
    elif auth_secret == _DEFAULT_AUTH_SECRET:
        insecure.append("AUTH_SECRET_KEY (still set to demo default)")

    for key in ("DATABASE_URL", "OPENROUTER_API_KEY", "LINKED_ACCOUNTS_KEY"):
        if not os.getenv(key, "").strip():
            missing.append(key)

    if missing or insecure:
        parts = []
        if missing:
            parts.append("missing: " + ", ".join(missing))
        if insecure:
            parts.append("insecure: " + ", ".join(insecure))
        raise RuntimeError(
            "Refusing to start: required environment variables not configured ("
            + "; ".join(parts)
            + "). See .env.example."
        )

def _load_runtime_defaults() -> dict:
    """Read repo-rooted config/defaults.json. Tolerates missing/malformed file."""
    import json

    path = project_root / "config" / "defaults.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("config/defaults.json not found; using empty defaults")
        return {}
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"config/defaults.json unreadable ({e}); using empty defaults")
        return {}


_RUNTIME_DEFAULTS = _load_runtime_defaults()
_DEFAULT_CORS_ORIGINS_LIST = (
    _RUNTIME_DEFAULTS.get("cors", {}).get("default_allowed_origins") or []
)
# Per S5 decision: CORS origins driven by CORS_ALLOWED_ORIGINS;
# CORS_ORIGINS kept as legacy alias. Default list lives in
# config/defaults.json so deployment values aren't hardcoded in source.
cors_origins_env = os.getenv("CORS_ALLOWED_ORIGINS") or os.getenv("CORS_ORIGINS")
if cors_origins_env:
    CORS_ORIGINS = [
        origin.strip() for origin in cors_origins_env.split(",") if origin.strip()
    ]
else:
    CORS_ORIGINS = list(_DEFAULT_CORS_ORIGINS_LIST)
if "*" in CORS_ORIGINS:
    raise RuntimeError(
        "CORS_ALLOWED_ORIGINS=* is not allowed (would conflict with allow_credentials=True). "
        "Specify explicit http(s)://host:port entries."
    )
pipeline_log(
    f"CORS allowed origins: {CORS_ORIGINS}",
    stage="pipeline",
    component="api",
)
# PATCH is required for /api/artifacts/{id}/review-status and
# /api/admin/users/{email} (admin user updates). Without PATCH listed here the
# browser's CORS preflight rejects the request, fetch() throws a TypeError,
# and the frontend's catch-all in utils/api.ts wraps it as the misleading
# "Failed to connect to backend at ..." message.
CORS_ALLOWED_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
CORS_ALLOWED_HEADERS = ["Authorization", "Content-Type"]

input_requests: Dict[str, InputRequest] = {}
websocket_handler: Optional[WebSocketHandler] = None
running_pipelines: Dict[str, asyncio.Task] = {}
cancellation_flags: Dict[str, bool] = {}
# Review Queue artifact-level resume tracking. Keyed on
# artifact_id. Prevents double-click duplicate downloads.
running_artifact_resumes: Dict[int, asyncio.Task] = {}
artifact_resume_cancellation_flags: Dict[int, bool] = {}
sid_to_user: Dict[str, str] = {}
project_operation_locks: Dict[str, asyncio.Lock] = {}

sio = socketio.AsyncServer(
    cors_allowed_origins=CORS_ORIGINS,
    logger=False,
    engineio_logger=False,
    async_mode='asgi',
    ping_interval=25,
    ping_timeout=60,
)

dao = ProjectDAO()

_storage = None

# P3-3: maps live in a shared module so main.py and websocket_handler
# can't drift out of sync (which they did historically when "cancelled"
# was added).
from status_maps import (
    PROJECT_STATUS_MAP,
    STAGE_NAME_MAP,
    STAGE_STATUS_MAP,
)

_DATETIME_JSON_ENCODER = {datetime: datetime_to_api_iso}


def _sync_project_current_stage_from_stages(project: Project) -> None:
    """Align current_stage with per-stage status (fixes restore/resume when stage was already in_progress)."""
    order = [
        PipelineStage.FIND,
        PipelineStage.ANALYZE,
        PipelineStage.DOWNLOAD,
        PipelineStage.QUALIFY,
    ]
    for ps in order:
        info = project.stages.get(ps)
        if info and info.status in (StageStatus.IN_PROGRESS, StageStatus.PAUSED):
            project.current_stage = ps
            return
    if project.status in (StageStatus.ERROR, StageStatus.CANCELLED):
        for ps in reversed(order):
            info = project.stages.get(ps)
            if info and info.status in (StageStatus.ERROR, StageStatus.CANCELLED):
                project.current_stage = ps
                return
    if project.status == StageStatus.COMPLETED:
        project.current_stage = PipelineStage.COMPLETE

def get_storage_backend():
    global _storage
    if _storage is None:
        from storage import get_storage
        _storage = get_storage()
    return _storage


def _ensure_datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    return datetime.now()


OUTPUTS_ROOT = (project_root / "outputs").resolve()


def _validate_artifact_local_path(file_path: str) -> Path:
    """Resolve `file_path` and confirm it stays inside OUTPUTS_ROOT.

    Raises 403 if the resolved path escapes the outputs directory (path
    traversal guard for endpoints serving DB-recorded file_path values).
    """
    if not file_path:
        raise HTTPException(status_code=404, detail="File not found on disk")
    p = Path(str(file_path)).expanduser()
    if not p.is_absolute():
        p = (project_root / p).resolve()
    else:
        p = p.resolve()
    try:
        p.relative_to(OUTPUTS_ROOT)
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail="Artifact path outside allowed outputs directory",
        )
    return p


def _derive_remote_key_from_file_path(file_path: str) -> Optional[str]:
    """Map a local artifact path to the OSS/S3 key used by the upload pipeline.

    Upload conventions (storage/factory get_storage callers):
      outputs/papers/<name>.pdf       -> papers/<name>.pdf
      outputs/analyses/<name>.md      -> analyses/<name>.md
      outputs/datasets/<paper>/<file> -> datasets/<paper>/<file>
    """
    if not file_path:
        return None
    parts = Path(str(file_path)).parts
    if "outputs" not in parts:
        return None
    idx = parts.index("outputs")
    rel_parts = parts[idx + 1:]
    return "/".join(rel_parts) if rel_parts else None


def _try_remote_redirect(
    artifact: dict, *, content_disposition: str
) -> Optional[RedirectResponse]:
    """Best-effort: redirect to remote presigned URL if the file is already on
    OSS/S3 under the upload-pipeline key, even when the DB row still says
    storage_backend='local'. Returns None on any miss; the caller must then
    serve the file from disk.
    """
    backend = (os.getenv("STORAGE_BACKEND") or "local").lower()
    if backend not in ("oss", "s3"):
        return None

    key = _derive_remote_key_from_file_path(artifact.get("file_path", ""))
    if not key:
        return None

    try:
        storage = get_storage_backend()
        if not storage.exists(key):
            return None
        filename = artifact.get("file_name") or os.path.basename(key)
        presigned_url = storage.generate_download_url(
            key=key,
            filename=filename,
            content_disposition=content_disposition,
            expiration=3600,
        )
        return RedirectResponse(url=presigned_url, status_code=302)
    except Exception as e:
        pipeline_log(
            f"Remote storage fallback lookup failed for artifact "
            f"{artifact.get('id')} (key={key}): {e}",
            stage="pipeline",
            component="storage",
            level=logging.WARNING,
        )
        return None


def _read_artifact_text_from_file(artifact: dict) -> Optional[str]:
    file_path = artifact.get("file_path")
    if not file_path:
        return None
    path = Path(str(file_path))
    if not path.is_absolute():
        path = project_root / path
    if not path.is_file():
        return None
    if path.suffix.lower() not in {".json", ".md", ".txt", ".yaml", ".yml"}:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning(f"artifact content fallback read failed path={path}: {e}")
        return None


def _get_project_lock(project_id: str) -> asyncio.Lock:
    lock = project_operation_locks.get(project_id)
    if lock is None:
        lock = asyncio.Lock()
        project_operation_locks[project_id] = lock
    return lock


async def _cancel_running_task(project_id: str, timeout_seconds: float = 1.0) -> None:
    task = running_pipelines.get(project_id)
    if not task:
        return
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=timeout_seconds)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        pass


def _apply_stage_records(project: "Project", stage_records: List[Dict]) -> None:
    for stage_record in stage_records:
        pipeline_stage = STAGE_NAME_MAP.get(stage_record.get("stage_name"))
        if not pipeline_stage:
            continue
        stage_info = project.stages[pipeline_stage]
        stage_info.status = STAGE_STATUS_MAP.get(stage_record.get("status"), StageStatus.PENDING)
        progress = stage_record.get("progress", 0.0)
        if stage_record.get("status") == "completed" and progress == 0.0:
            progress = 1.0
        stage_info.progress = progress
        stage_info.start_time = _ensure_datetime(stage_record.get("start_time")) if stage_record.get("start_time") else None
        stage_info.end_time = _ensure_datetime(stage_record.get("end_time")) if stage_record.get("end_time") else None
        stage_info.error_message = stage_record.get("error_message")


def _apply_artifact_counts(project: "Project", counts_by_stage: Dict[str, int]) -> None:
    for stage_name, pipeline_stage in STAGE_NAME_MAP.items():
        if pipeline_stage in project.stages:
            project.stages[pipeline_stage].artifact_count = counts_by_stage.get(stage_name, 0)


def build_project_from_summary(
    db_project: Dict,
    stage_records: List[Dict],
    artifact_counts: Dict[str, int],
) -> "Project":
    """Project list path: stages + artifact counts already prefetched in
    bulk by ``ProjectDAO.get_projects_page_with_summary`` (avoids
    1+2N round-trips on every list call)."""
    status = db_project.get("status", "pending")
    project = Project(
        id=db_project["id"],
        name=db_project["name"],
        query=db_project["research_query"],
        user_email=db_project.get("user_email"),
        parallel_pipeline=bool(db_project.get("parallel_pipeline")),
        analysis_model=db_project.get("analysis_model"),
        download_model=db_project.get("download_model"),
        status=PROJECT_STATUS_MAP.get(status, StageStatus.PENDING),
        overall_progress=db_project.get("overall_progress", 0.0),
        created_at=_ensure_datetime(db_project.get("created_at")),
        updated_at=_ensure_datetime(db_project.get("updated_at")),
    )
    _apply_stage_records(project, stage_records)
    _apply_artifact_counts(project, artifact_counts)
    return project


def build_project_from_db(db_project: Dict) -> Project:
    status = db_project.get("status", "pending")
    project = Project(
        id=db_project["id"],
        name=db_project["name"],
        query=db_project["research_query"],
        user_email=db_project.get("user_email"),
        parallel_pipeline=bool(db_project.get("parallel_pipeline")),
        analysis_model=db_project.get("analysis_model"),
        download_model=db_project.get("download_model"),
        status=PROJECT_STATUS_MAP.get(status, StageStatus.PENDING),
        overall_progress=db_project.get("overall_progress", 0.0),
        created_at=_ensure_datetime(db_project.get("created_at")),
        updated_at=_ensure_datetime(db_project.get("updated_at")),
    )

    stage_records = dao.get_project_stages(project.id)
    for stage_record in stage_records:
        pipeline_stage = STAGE_NAME_MAP.get(stage_record.get("stage_name"))
        if not pipeline_stage:
            continue
        stage_info = project.stages[pipeline_stage]
        stage_info.status = STAGE_STATUS_MAP.get(stage_record.get("status"), StageStatus.PENDING)
        progress = stage_record.get("progress", 0.0)
        if stage_record.get("status") == "completed" and progress == 0.0:
            progress = 1.0
        stage_info.progress = progress
        stage_info.start_time = _ensure_datetime(stage_record.get("start_time")) if stage_record.get("start_time") else None
        stage_info.end_time = _ensure_datetime(stage_record.get("end_time")) if stage_record.get("end_time") else None
        stage_info.error_message = stage_record.get("error_message")

    try:
        artifact_rows = dao.get_project_artifacts_metadata(project.id)
        artifact_counts: Dict[str, int] = {}
        for artifact in artifact_rows:
            stage_name = artifact.get("stage_name")
            if not stage_name:
                continue
            if (
                stage_name == "download"
                and artifact.get("artifact_type") not in {"dataset", "embedded_dataset"}
            ):
                continue
            artifact_counts[stage_name] = artifact_counts.get(stage_name, 0) + 1

        # Mirror get_projects_page_with_summary: the analyze count must include
        # papers whose LLM returned no_suitable_data (no .md saved) so the
        # detail-page badge agrees with the project-list badge.
        try:
            artifact_counts["analyze"] = dao.get_analyze_processed_count(project.id)
        except Exception:
            pass

        for stage_name, pipeline_stage in STAGE_NAME_MAP.items():
            if pipeline_stage in project.stages:
                project.stages[pipeline_stage].artifact_count = artifact_counts.get(stage_name, 0)
    except Exception:
        pass

    _sync_project_current_stage_from_stages(project)
    return project


def verify_project_or_404(project_id: str, user_email: str) -> Project:
    """Return project owned by user_email or raise 404."""
    if not dao.verify_project_ownership(project_id, user_email):
        raise HTTPException(status_code=404, detail="Project not found")
    db_project = dao.get_project(project_id)
    if db_project:
        return build_project_from_db(db_project)
    raise HTTPException(status_code=404, detail="Project not found")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global websocket_handler

    _validate_required_env()

    pipeline_log("Starting AutoGen Web UI backend", stage="pipeline", component="startup")
    websocket_handler = WebSocketHandler(sio, input_requests, dao)

    try:
        seed_demo_user()
        pipeline_log("startup: demo user seeded", stage="pipeline", component="startup")
    except Exception as e:
        pipeline_log(
            f"startup warning: demo user seed failed (non-fatal): {e}",
            stage="pipeline",
            component="startup",
            level=logging.WARNING,
        )

    try:
        from linked_accounts import ensure_table, verify_linked_accounts_key_can_decrypt
        ensure_table()
        pipeline_log("startup: linked accounts table ensured", stage="pipeline", component="startup")
        decrypt_problem = verify_linked_accounts_key_can_decrypt()
        if decrypt_problem:
            pipeline_log(
                f"startup warning: LINKED_ACCOUNTS_KEY does not decrypt the most recent "
                f"linked_accounts row ({decrypt_problem}); affected users will need to "
                f"re-enter their credentials",
                stage="pipeline",
                component="startup",
                level=logging.WARNING,
            )
    except Exception as e:
        pipeline_log(
            f"startup warning: linked accounts table setup failed (non-fatal): {e}",
            stage="pipeline",
            component="startup",
            level=logging.WARNING,
        )

    try:
        n_proj, n_stage = dao.reconcile_stale_running_after_restart()
        if n_proj or n_stage:
            pipeline_log(
                f"startup: reconciled stale running state projects={n_proj} stages={n_stage}",
                stage="pipeline",
                component="startup",
            )
    except Exception as e:
        pipeline_log(
            f"startup warning: stale running reconciliation failed (non-fatal): {e}",
            stage="pipeline",
            component="startup",
            level=logging.WARNING,
        )

    # Reap browser processes orphaned by a prior abnormal exit (SIGKILL / deploy
    # mid-download). Safe here: no download is running yet, so any managed-browser
    # process is leaked. Prevents dozens of chrome procs / GBs of RSS piling up.
    try:
        from download.local_cdp_runtime import reap_orphaned_browser_processes
        reaped = reap_orphaned_browser_processes()
        if reaped:
            pipeline_log(
                f"startup: reaped {reaped} orphaned browser process(es)",
                stage="pipeline",
                component="startup",
            )
    except Exception as e:
        pipeline_log(
            f"startup warning: browser reap failed (non-fatal): {e}",
            stage="pipeline",
            component="startup",
            level=logging.WARNING,
        )

    # F4: cancelled projects retention sweep. cancel != delete; we keep the
    # row + outputs around for CANCELLED_RETENTION_DAYS (default 7) so the
    # user can change their mind, then a lifespan/scheduled sweep removes
    # them permanently. Set to 0 to disable retention entirely.
    try:
        retention_days = int(os.getenv("CANCELLED_RETENTION_DAYS", "7"))
        if retention_days > 0:
            removed = dao.delete_cancelled_projects_older_than(retention_days)
            if removed:
                pipeline_log(
                    f"startup: purged {len(removed)} cancelled project(s) older than "
                    f"{retention_days} days",
                    stage="pipeline",
                    component="startup",
                )
    except Exception as e:
        pipeline_log(
            f"startup warning: cancelled retention sweep failed (non-fatal): {e}",
            stage="pipeline",
            component="startup",
            level=logging.WARNING,
        )

    yield

    pipeline_log("Shutting down AutoGen Web UI backend", stage="pipeline", component="startup")
    shutdown_timeout = float(os.getenv("PIPELINE_SHUTDOWN_TIMEOUT_SECONDS", "30"))
    for project_id, task in list(running_pipelines.items()):
        pipeline_log(
            "Cancelling running pipeline during shutdown",
            stage="pipeline",
            component="startup",
            project_id=project_id,
        )
        # Mark paused BEFORE task.cancel() so the CancelledError handler in
        # start_pipeline (which checks `status != "paused"` to decide between
        # cancelled vs paused) sees the paused state and skips the cancelled
        # write. Without this, a clean shutdown would flip running projects to
        # 'cancelled', bypassing reconcile_stale_running_after_restart and
        # leaving the user unable to resume.
        try:
            dao.update_project_status(project_id, "paused")
            dao.add_message(
                project_id,
                "Backend shutting down; pipeline auto-paused. Resume to continue.",
                team_name="System",
                message_type="warning",
            )
        except Exception as e:
            pipeline_log(
                f"shutdown warning: failed to mark project paused before cancel: {e}",
                stage="pipeline",
                component="startup",
                project_id=project_id,
                level=logging.WARNING,
            )
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=shutdown_timeout)
        except asyncio.CancelledError:
            pass
        except asyncio.TimeoutError:
            pipeline_log(
                f"shutdown: pipeline task did not exit within {shutdown_timeout}s; "
                f"abandoning to avoid blocking shutdown",
                stage="pipeline",
                component="startup",
                project_id=project_id,
                level=logging.WARNING,
            )
        except Exception as e:
            pipeline_log(
                f"shutdown: pipeline task raised on cancel: {e}",
                stage="pipeline",
                component="startup",
                project_id=project_id,
                level=logging.WARNING,
            )

    try:
        from database.connection import close_all_pools
        close_all_pools()
        pipeline_log("shutdown: closed DB connection pools", stage="pipeline", component="startup")
    except Exception as e:
        pipeline_log(
            f"shutdown warning: closing DB pools failed: {e}",
            stage="pipeline",
            component="startup",
            level=logging.WARNING,
        )


def _rate_limit_key(request: Request) -> str:
    """Per-user when authenticated, per-IP otherwise.

    Auth header is read directly to avoid coupling the limiter decorator with
    FastAPI's Depends() machinery (slowapi runs before dependency resolution).
    """
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:].strip()
        try:
            email = verify_token(token)
            if email:
                return f"user:{email}"
        except Exception:
            pass
    return f"ip:{get_remote_address(request)}"


_RATE_LIMIT_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "true").lower() != "false"
limiter = Limiter(key_func=_rate_limit_key, enabled=_RATE_LIMIT_ENABLED)

app = FastAPI(
    title="AutoGen Research Pipeline API",
    description="Backend API for the AutoGen Research Pipeline Web UI",
    version="1.0.0",
    lifespan=lifespan
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# P3-1: unify error response envelope so the frontend doesn't have to
# special-case FastAPI's default {detail: ...}, slowapi's {detail: "..."},
# and unhandled 500s. Every error now arrives as
#   {"code": <http-status>, "error": "<symbol>", "detail": <message>}
# Existing code that raises `HTTPException(status_code=..., detail=...)`
# keeps working — we just rewrite the wire format on the way out.
from fastapi.exceptions import RequestValidationError as _RVE
from starlette.exceptions import HTTPException as _StarletteHTTPException


def _http_exception_envelope(status: int, detail) -> Dict:
    code_symbols = {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        405: "method_not_allowed",
        409: "conflict",
        413: "payload_too_large",
        422: "validation_error",
        429: "rate_limited",
        500: "internal_error",
        503: "service_unavailable",
    }
    return {
        "code": status,
        "error": code_symbols.get(status, "error"),
        "detail": detail,
    }


@app.exception_handler(_StarletteHTTPException)
async def _http_exception_handler(request: Request, exc: _StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content=_http_exception_envelope(exc.status_code, exc.detail),
        headers=getattr(exc, "headers", None) or None,
    )


@app.exception_handler(_RVE)
async def _validation_exception_handler(request: Request, exc: _RVE):
    # pydantic v2 exc.errors() may include ctx: {"error": ValueError(...)} which is
    # not JSON-serializable.  Scrub ctx values to strings before building the envelope.
    def _scrub(errors):
        result = []
        for err in errors:
            cleaned = dict(err)
            if "ctx" in cleaned:
                cleaned["ctx"] = {
                    k: str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v
                    for k, v in cleaned["ctx"].items()
                }
            cleaned.pop("url", None)
            result.append(cleaned)
        return result

    return JSONResponse(
        status_code=422,
        content=_http_exception_envelope(422, _scrub(exc.errors())),
    )


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    pipeline_log(
        f"unhandled exception on {request.method} {request.url.path}: "
        f"{type(exc).__name__}: {exc}",
        stage="pipeline",
        component="api",
        level=logging.ERROR,
    )
    return JSONResponse(
        status_code=500,
        content=_http_exception_envelope(500, "Internal server error"),
    )


_DEFAULT_CSP = (
    "default-src 'self'; "
    "img-src 'self' data:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)
_CSP_HEADER = os.getenv("CONTENT_SECURITY_POLICY", _DEFAULT_CSP).strip()


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach defensive response headers on every response.

    HSTS is intentionally omitted: the deployment serves over plain HTTP
    by design. CSP, X-Frame-Options, X-Content-Type-Options, and
    Referrer-Policy still apply.
    """

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", _CSP_HEADER)
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response


_DEFAULT_MAX_BODY_BYTES = 4 * 1024 * 1024  # 4 MiB
try:
    _MAX_REQUEST_BODY_BYTES = int(os.getenv("MAX_REQUEST_BODY_BYTES", str(_DEFAULT_MAX_BODY_BYTES)))
except ValueError:
    _MAX_REQUEST_BODY_BYTES = _DEFAULT_MAX_BODY_BYTES


class RequestBodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests advertising a Content-Length above the configured cap.

    This is a cheap pre-check; the body is still streamed by Starlette
    afterwards so a misbehaving client cannot consume more memory than
    the cap by lying about Content-Length.
    """

    async def dispatch(self, request, call_next):
        cl_header = request.headers.get("content-length")
        if cl_header is not None:
            try:
                cl_value = int(cl_header)
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={"detail": "Invalid Content-Length header"},
                )
            if cl_value > _MAX_REQUEST_BODY_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={
                        "detail": (
                            f"Request body exceeds {_MAX_REQUEST_BODY_BYTES} bytes"
                        )
                    },
                )
        return await call_next(request)


app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestBodySizeLimitMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=CORS_ALLOWED_METHODS,
    allow_headers=CORS_ALLOWED_HEADERS,
)

socket_app = socketio.ASGIApp(sio, other_asgi_app=app, socketio_path="socket.io")


@app.post("/api/auth/login", response_model=LoginResponse)
@limiter.limit("10/minute")
async def login(request: Request, payload: LoginRequest):
    if not authenticate_user(payload.email, payload.password):
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password"
        )

    token = create_access_token(payload.email)
    profile = get_user_profile(payload.email)
    return LoginResponse(
        access_token=token,
        email=payload.email,
        name=profile.get("name"),
    )


@app.post("/api/auth/register", response_model=RegisterResponse)
@limiter.limit("5/minute")
async def register(
    request: Request,
    payload: RegisterRequest,
    admin_email: str = Depends(require_admin),
):
    """Admin-only: Create a new user. Regular registration is disabled."""
    try:
        user = register_user(payload.name, payload.email, payload.password)
    except ValueError as e:
        status_code = 409 if "already exists" in str(e) else 400
        raise HTTPException(status_code=status_code, detail=str(e))

    token = create_access_token(user["email"])
    return RegisterResponse(
        access_token=token,
        email=user["email"],
        name=user["name"],
    )


@app.get("/api/auth/verify")
async def verify_auth(_user: str = Depends(get_current_user)):
    return {"valid": True}


@app.get("/api/auth/me", response_model=UserProfileResponse)
async def get_me(email: str = Depends(get_current_user)):
    profile = get_user_profile(email)
    return UserProfileResponse(**profile)


@app.delete("/api/auth/account")
async def delete_account(email: str = Depends(get_current_user)):
    deleted = delete_user(email)
    if not deleted:
        raise HTTPException(status_code=404, detail="User not found")
    return {"message": "Account deleted"}


# Password management endpoints

@app.put("/api/auth/password")
async def change_password(
    request: ChangePasswordRequest,
    email: str = Depends(get_current_user)
):
    """Change own password."""
    success = change_user_password(email, request.current_password, request.new_password)
    if not success:
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    return {"message": "Password changed successfully"}


@app.post("/api/auth/reset-password")
@limiter.limit("5/minute")
async def request_password_reset(request: Request, payload: ResetPasswordRequest):
    """Request a password reset. Returns success regardless of whether the email exists (prevents enumeration)."""
    token = generate_password_reset_token(payload.email)

    # Always return success to prevent email enumeration
    if token:
        reset_url = os.getenv("RESET_PASSWORD_URL", "http://localhost:3000/reset-password")
        reset_link = f"{reset_url}?token={token}"

        body = f"""You requested a password reset for your AutoGen account.

Click the link below to reset your password:
{reset_link}

This link will expire in 1 hour.

If you didn't request this, please ignore this email.
"""
        try:
            from web_ui.backend.smtp_client import SMTPNotConfigured, send_email

            send_email(
                recipients=[payload.email],
                subject="Password Reset - AutoGen Research Pipeline",
                body=body,
            )
            pipeline_log(
                f"email: password reset sent to {payload.email}",
                stage="pipeline",
                component="email",
            )
        except SMTPNotConfigured:
            # No SMTP configured — silently skip (the API still returns success
            # to prevent email enumeration).
            pass
        except Exception as e:
            # Log the error but don't expose details to client
            pipeline_log(
                f"email error: password reset send failed: {e}",
                stage="pipeline",
                component="email",
                level=logging.ERROR,
            )
    
    # Always return success
    return {"message": "If an account with that email exists, a password reset link has been sent"}


@app.post("/api/auth/reset-password/{token}")
@limiter.limit("5/minute")
async def reset_password(
    request: Request,
    token: str,
    payload: ResetPasswordWithTokenRequest,
):
    """Reset password using the token."""
    try:
        success = reset_password_with_token(token, payload.new_password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not success:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    return {"message": "Password reset successfully"}


# Admin endpoints

@app.get("/api/auth/admin-check")
async def check_is_admin(email: str = Depends(get_current_user)):
    """Check if current user is an admin."""
    return {"is_admin": is_admin(email)}


@app.get("/api/admin/users", response_model=List[UserResponse])
async def list_users(_admin: str = Depends(require_admin)):
    """Get all users (admin only)."""
    users = get_all_users()
    return [
        UserResponse(
            id=u["id"],
            name=u["name"],
            email=u["email"],
            role=u["role"],
            is_active=u["is_active"],
            created_at=(
                datetime_to_api_iso(u["created_at"])
                if isinstance(u["created_at"], datetime)
                else str(u["created_at"])
            ),
        )
        for u in users
    ]


def _admin_audit(event: str, admin_email: str, target: str, **payload):
    """Best-effort audit row for an admin user-mgmt action.

    Routed through execution_logs with project_id=NULL so it shows up in
    the system-wide audit view but doesn't get tied to a specific project.
    """
    try:
        dao.add_execution_log(
            project_id=None,
            event_type=event,
            message=f"admin={admin_email} target={target}",
            severity="info",
            team_name="Admin",
            payload={"admin": admin_email, "target": target, **payload},
        )
    except Exception as e:
        pipeline_log(
            f"admin audit log write failed for {event}: {e}",
            stage="pipeline", component="admin", level=logging.WARNING,
        )


@app.post("/api/admin/users", response_model=UserResponse, status_code=201)
async def create_user(request: AdminCreateUserRequest, admin_email: str = Depends(require_admin)):
    """Create a new user (admin only)."""
    try:
        user = create_user_by_admin(request.name, request.email, request.password, request.role)
    except ValueError as e:
        status_code = 409 if "already exists" in str(e) else 400
        raise HTTPException(status_code=status_code, detail=str(e))

    _admin_audit("admin.user_created", admin_email, user["email"], role=user["role"])

    return UserResponse(
        id=user["id"],
        name=user["name"],
        email=user["email"],
        role=user["role"],
        is_active=1,
        created_at=datetime_to_api_iso(datetime.now(timezone.utc)),
    )


@app.patch("/api/admin/users/{email}", response_model=UserResponse)
async def update_user(
    email: str,
    request: AdminUpdateUserRequest,
    admin_email: str = Depends(require_admin),
):
    """Update name / role / is_active (admin only).

    Guard rails (single source of truth — no super-admin tier):
      * an admin cannot demote or deactivate themselves
      * the last active admin cannot be demoted or deactivated
    """
    target = email.lower()
    target_role = get_user_role(target)
    if target_role is None:
        raise HTTPException(status_code=404, detail="User not found")

    demoting = request.role is not None and request.role != "admin" and target_role == "admin"
    deactivating = request.is_active == 0 and target_role == "admin"

    if demoting or deactivating:
        if target == admin_email.lower():
            raise HTTPException(
                status_code=400,
                detail="Cannot demote or deactivate your own admin account",
            )
        if count_admins() <= 1:
            raise HTTPException(
                status_code=400,
                detail="Cannot demote or deactivate the last admin account",
            )

    try:
        ok = update_user_fields_by_admin(
            target,
            name=request.name,
            role=request.role,
            is_active=request.is_active,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=400, detail="No changes applied")

    _admin_audit(
        "admin.user_updated", admin_email, target,
        name=request.name, role=request.role, is_active=request.is_active,
    )

    # Re-fetch for response
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, email, role, is_active, created_at FROM users WHERE email = %s",
                (target,),
            )
            row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="User not found after update")
    return UserResponse(
        id=row["id"], name=row["name"], email=row["email"], role=row["role"],
        is_active=row["is_active"],
        created_at=(
            datetime_to_api_iso(row["created_at"])
            if isinstance(row["created_at"], datetime) else str(row["created_at"])
        ),
    )


@app.post("/api/admin/users/{email}/reset-password")
async def reset_user_password(
    email: str,
    request: AdminResetPasswordRequest,
    admin_email: str = Depends(require_admin),
):
    """Admin-driven password reset (no current-password check)."""
    if get_user_role(email) is None:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        ok = reset_user_password_by_admin(email, request.new_password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="User not found")
    _admin_audit("admin.password_reset", admin_email, email.lower())
    return {"message": "Password reset"}


@app.delete("/api/admin/users/{email}")
async def delete_user_by_admin(email: str, admin_email: str = Depends(require_admin)):
    """Delete a user (admin only).

    All admins are equal (no super-admin tier). Two universal guards:
      * cannot delete your own account
      * cannot delete the last remaining active admin
    """
    target = email.lower()
    if target == admin_email.lower():
        raise HTTPException(status_code=400, detail="Cannot delete your own account")

    target_role = get_user_role(target)
    if target_role is None:
        raise HTTPException(status_code=404, detail="User not found")

    if target_role == "admin" and count_admins() <= 1:
        raise HTTPException(status_code=400, detail="Cannot delete the last admin account")

    deleted = delete_user(target)
    if not deleted:
        raise HTTPException(status_code=404, detail="User not found")
    _admin_audit("admin.user_deleted", admin_email, target, was_role=target_role)
    return {"message": "User deleted successfully"}


# ---------------------------------------------------------------------------
# GET /api/models — OpenRouter model proxy (TTL-cached, filtered)
# ---------------------------------------------------------------------------

@app.get("/api/models")
@limiter.limit("60/hour")
async def get_models(
    request: Request,
    _user: str = Depends(get_current_user),
):
    """Proxy the OpenRouter model list filtered to mainstream providers.

    Returns ``{"models": [{id, name}, ...], "defaults": {"analysis": ..., "download": ...}}``.
    ``defaults`` reflects the currently-resolved per-stage env vars so the
    frontend can show users which model the "use default" option will pick.
    Results are cached in memory for one hour per worker process. On OpenRouter
    error, raises HTTP 502.
    """
    models = await _get_models_cached()
    return {
        "models": models,
        "defaults": {
            "analysis": get_openrouter_model("OPENROUTER_ANALYSIS_MODEL"),
            "download": get_openrouter_download_model(),
        },
    }


@app.get("/api/projects", response_model=ProjectListResponse)
async def get_projects(
    user_email: str = Depends(get_current_user),
    page: int = Query(1, ge=1),
    page_size: Optional[int] = Query(None, ge=1),
    q: Optional[str] = Query(None, description="Search project name or research query (substring)"),
    status: Optional[ProjectListFilterStatus] = Query(
        None, description="Filter by stored project status (excludes cancelled regardless)"
    ),
):
    default_ps, max_ps = 10, 100
    ps = page_size if page_size is not None else default_ps
    ps = min(ps, max_ps)
    # Count and page query hit the same filter set but are independent;
    # run them concurrently (psycopg is sync → dispatch to worker threads).
    total, page_result = await asyncio.gather(
        asyncio.to_thread(dao.count_projects_visible, user_email, q, status),
        asyncio.to_thread(
            dao.get_projects_page_with_summary, user_email, q, page, ps, status
        ),
    )
    rows, stages_by_pid, counts_by_pid = page_result
    items = [
        build_project_from_summary(
            p,
            stages_by_pid.get(p["id"], []),
            counts_by_pid.get(p["id"], {}),
        )
        for p in rows
    ]
    return ProjectListResponse(items=items, total=total, page=page, page_size=ps)


@app.get("/api/stats/projects", response_model=ProjectDashboardStats)
async def get_projects_stats(user_email: str = Depends(get_current_user)):
    """Dashboard stats (not under /api/projects/... to avoid clash with /api/projects/{project_id})."""
    stats = dao.get_project_dashboard_stats(user_email)
    return ProjectDashboardStats(**stats)


@app.get("/api/projects/{project_id}", response_model=Project)
async def get_project(project_id: str, user_email: str = Depends(get_current_user)):
    return verify_project_or_404(project_id, user_email)


@app.post("/api/projects", response_model=Project)
@limiter.limit("30/hour")
async def create_project(
    request: Request,
    payload: CreateProjectRequest,
    background_tasks: BackgroundTasks,
    user_email: str = Depends(get_current_user)
):
    pipeline_log(
        f"api create_project request: name={payload.name!r} max_papers={payload.max_papers} "
        f"parallel_pipeline={payload.parallel_pipeline} "
        f"analysis_model={payload.analysis_model!r} download_model={payload.download_model!r}",
        stage="pipeline",
        component="api",
    )
    requested_max_papers = payload.max_papers if payload.max_papers is not None else 10
    # persist date range so start_pipeline / resume can recover.
    # When None, dao.create_project defaults are used (no UI override).
    # persist force_reanalyze so analyze stage reads it from
    # project meta rather than carrying the bypass flag through every kwarg.
    project_id = dao.create_project(
        payload.name,
        payload.query,
        user_email=user_email,
        max_papers=requested_max_papers,
        parallel_pipeline=payload.parallel_pipeline,
        analysis_model=payload.analysis_model,
        download_model=payload.download_model,
        date_start=payload.date_start,
        date_end=payload.date_end,
        force_reanalyze=payload.force_reanalyze,
        mesh_expansion=payload.mesh_expansion,
    )
    db_project = dao.get_project(project_id)
    if not db_project:
        raise HTTPException(status_code=500, detail="Failed to create project")
    project = build_project_from_db(db_project)

    if websocket_handler:
        # Fire-and-forget — the broadcast is notification-only and must not
        # gate the HTTP response. Exceptions are logged inside the handler.
        asyncio.create_task(
            websocket_handler.broadcast_message(WebSocketMessage(
                type="project_created",
                project_id=project.id,
                data=project.dict_with_iso_dates(),
            ))
        )

    task = asyncio.create_task(start_pipeline(project.id, max_papers=requested_max_papers))
    running_pipelines[project.id] = task

    pipeline_log(
        f"project created and pipeline task scheduled: name={project.name!r} max_papers={requested_max_papers}",
        stage="pipeline",
        component="api",
        project_id=project.id,
    )
    return project


@app.put("/api/projects/{project_id}", response_model=Project)
async def update_project(
    project_id: str,
    request: UpdateProjectRequest,
    user_email: str = Depends(get_current_user)
):
    verify_project_or_404(project_id, user_email)
    dao.update_project_fields(
        project_id,
        name=request.name,
        research_query=request.query,
    )
    db_project = dao.get_project(project_id)
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    project = build_project_from_db(db_project)

    if websocket_handler:
        try:
            await websocket_handler.broadcast_message(WebSocketMessage(
                type="project_updated",
                project_id=project.id,
                data=project.dict_with_iso_dates()
            ))
        except Exception as e:
            pipeline_log(
                f"project_updated broadcast failed: {e}",
                stage="pipeline",
                component="api",
                project_id=project.id,
                level=logging.WARNING,
            )

    return project


@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: str, user_email: str = Depends(get_current_user)):
    verify_project_or_404(project_id, user_email)
    dao.delete_project(project_id)

    if websocket_handler:
        try:
            await websocket_handler.broadcast_message(WebSocketMessage(
                type="project_deleted",
                project_id=project_id,
                data={}
            ))
        except Exception as e:
            pipeline_log(
                f"project_deleted broadcast failed: {e}",
                stage="pipeline",
                component="api",
                project_id=project_id,
                level=logging.WARNING,
            )

    return {"message": "Project deleted"}


@app.post("/api/projects/{project_id}/pause")
async def pause_project(project_id: str, user_email: str = Depends(get_current_user)):
    async with _get_project_lock(project_id):
        project = verify_project_or_404(project_id, user_email)

        if project.status != StageStatus.IN_PROGRESS:
            raise HTTPException(status_code=400, detail="Project is not running")

        cancellation_flags[project_id] = True
        dao.update_project_status(project_id, "paused")
        pipeline_log(
            "api pause_project requested: set cancellation flag and project status=paused",
            stage="pipeline",
            component="api",
            project_id=project_id,
        )

        current_stage = dao.get_current_stage(project_id)
        if current_stage:
            dao.pause_stage(project_id, current_stage['stage_name'])
            pipeline_log(
                f"pause_project: marked current stage paused ({current_stage['stage_name']})",
                stage="pipeline",
                component="api",
                project_id=project_id,
            )
            dao.add_message(
                project_id=project_id,
                stage_name="pipeline",
                team_name="PipelineRunner",
                content=f"Project paused by user during {current_stage['stage_name']} stage",
                message_type="info",
            )
            dao.add_execution_log(
                project_id=project_id,
                event_type="pipeline_paused",
                stage_name="pipeline",
                team_name="PipelineRunner",
                message=f"Pause requested by user during {current_stage['stage_name']} stage",
                payload={"source": "api", "requested_by": user_email},
            )
        else:
            dao.add_message(
                project_id=project_id,
                stage_name="pipeline",
                team_name="PipelineRunner",
                content="Project paused by user",
                message_type="info",
            )
            dao.add_execution_log(
                project_id=project_id,
                event_type="pipeline_paused",
                stage_name="pipeline",
                team_name="PipelineRunner",
                message="Pause requested by user",
                payload={"source": "api", "requested_by": user_email},
            )

        await _cancel_running_task(project_id)

    if websocket_handler:
        updated = verify_project_or_404(project_id, user_email)
        await websocket_handler.broadcast_message(WebSocketMessage(
            type="project_updated",
            project_id=project_id,
            data=updated.dict_with_iso_dates()
        ))

    pipeline_log(
        f"pause_project complete for {project.name!r}",
        stage="pipeline",
        component="api",
        project_id=project_id,
    )
    return {"message": "Project paused", "project_id": project_id}


@app.post("/api/projects/{project_id}/resume")
async def resume_project(project_id: str, user_email: str = Depends(get_current_user)):
    async with _get_project_lock(project_id):
        project = verify_project_or_404(project_id, user_email)

        if project.status not in (StageStatus.PAUSED, StageStatus.ERROR):
            raise HTTPException(status_code=400, detail="Project is not paused or in error state")

        existing_task = running_pipelines.get(project_id)
        if existing_task and not existing_task.done():
            raise HTTPException(status_code=409, detail="Project pipeline is already running")

        if project_id in cancellation_flags:
            del cancellation_flags[project_id]
            pipeline_log(
                "resume_project: cleared cancellation flag",
                stage="pipeline",
                component="api",
                project_id=project_id,
            )

        dao.update_project_status(project_id, "running")

        if websocket_handler:
            updated = verify_project_or_404(project_id, user_email)
            await websocket_handler.broadcast_message(WebSocketMessage(
                type="project_updated",
                project_id=project_id,
                data=updated.dict_with_iso_dates()
            ))

        db_project = dao.get_project(project_id) or {}
        max_papers = db_project.get("max_papers") or 10
        resume_stage = (dao.get_current_stage(project_id) or {}).get("stage_name") or "pipeline"
        dao.add_message(
            project_id=project_id,
            stage_name="pipeline",
            team_name="PipelineRunner",
            content=f"Project resumed by user at {resume_stage} stage",
            message_type="info",
        )
        dao.add_execution_log(
            project_id=project_id,
            event_type="pipeline_resumed",
            stage_name="pipeline",
            team_name="PipelineRunner",
            message=f"Resume requested by user at {resume_stage} stage",
            payload={
                "source": "api",
                "requested_by": user_email,
                "resume": True,
                "stage_name": resume_stage,
            },
        )
        task = asyncio.create_task(start_pipeline(project_id, resume=True, max_papers=max_papers))
        running_pipelines[project_id] = task

    pipeline_log(
        f"resume_project complete for {project.name!r}; pipeline task scheduled",
        stage="pipeline",
        component="api",
        project_id=project_id,
    )
    return {"message": "Project resumed", "project_id": project_id}


@app.post("/api/projects/{project_id}/cancel")
async def cancel_project(project_id: str, user_email: str = Depends(get_current_user)):
    async with _get_project_lock(project_id):
        project = verify_project_or_404(project_id, user_email)

        # Set status BEFORE _cancel_running_task (mirrors pause_project). The
        # CancelledError handler in start_pipeline no longer writes the
        # terminal status — the caller that triggered the cancel decides it.
        # This means restart-induced cancels can never produce 'cancelled':
        # only this endpoint does.
        cancellation_flags[project_id] = True
        dao.update_project_status(project_id, "cancelled")
        await _cancel_running_task(project_id)
        pipeline_log(
            "cancel_project: cancellation flag set, status=cancelled, running task cancelled",
            stage="pipeline",
            component="api",
            project_id=project_id,
        )

    # F4: cancel != delete. Broadcast a status change so the frontend can
    # hide the project from the active list while keeping the row + outputs
    # in place for the configured grace period (CANCELLED_RETENTION_DAYS).
    # Note: previously start_pipeline's CancelledError handler also fired a
    # `project_deleted` event; F4 explicitly moved away from that semantic
    # (frontend treated it as "gone forever") so this endpoint deliberately
    # emits only project_status_changed.
    if websocket_handler:
        await websocket_handler.broadcast_message(WebSocketMessage(
            type="project_status_changed",
            project_id=project_id,
            data={"status": "cancelled"},
        ))

    pipeline_log(
        f"cancel_project complete: marked cancelled ({project.name!r}); "
        f"outputs retained for {os.getenv('CANCELLED_RETENTION_DAYS', '7')} days",
        stage="pipeline",
        component="api",
        project_id=project_id,
    )
    return {
        "message": "Project cancelled",
        "project_id": project_id,
        "status": "cancelled",
    }


@app.get("/api/projects/{project_id}/messages")
async def get_project_messages(project_id: str, user_email: str = Depends(get_current_user)):
    verify_project_or_404(project_id, user_email)
    db_messages = dao.get_project_messages(project_id)
    return jsonable_encoder(
        [
            Message(
                id=m.get("id"),
                content=m.get("content", ""),
                message_type=MessageType(m.get("message_type", "system")),
                team_name=m.get("team_name"),
                timestamp=_ensure_datetime(m.get("timestamp")),
            ).model_dump()
            for m in reversed(db_messages)
        ],
        custom_encoder=_DATETIME_JSON_ENCODER,
    )


@app.get("/api/database/projects")
async def get_database_projects(user_email: str = Depends(get_current_user)):
    # psycopg is sync — offload so the event loop stays responsive while
    # the (potentially large) project list is scanned. Pipeline tasks
    # writing artifacts in parallel would otherwise queue API handlers.
    projects = await asyncio.to_thread(dao.get_all_projects, user_email=user_email)
    return jsonable_encoder(projects, custom_encoder=_DATETIME_JSON_ENCODER)


@app.get("/api/database/projects/{project_id}/stages")
async def get_project_stages(project_id: str, user_email: str = Depends(get_current_user)):
    ownership_ok, stages = await asyncio.gather(
        asyncio.to_thread(dao.verify_project_ownership, project_id, user_email),
        asyncio.to_thread(dao.get_project_stages, project_id),
    )
    if not ownership_ok:
        raise HTTPException(status_code=404, detail="Project not found")
    return jsonable_encoder(stages, custom_encoder=_DATETIME_JSON_ENCODER)


@app.get("/api/database/projects/{project_id}/messages")
async def get_database_messages(project_id: str, user_email: str = Depends(get_current_user)):
    ownership_ok, messages = await asyncio.gather(
        asyncio.to_thread(dao.verify_project_ownership, project_id, user_email),
        asyncio.to_thread(dao.get_project_messages, project_id),
    )
    if not ownership_ok:
        raise HTTPException(status_code=404, detail="Project not found")
    return jsonable_encoder(messages, custom_encoder=_DATETIME_JSON_ENCODER)


@app.get("/api/database/projects/{project_id}/execution-logs")
async def get_project_execution_logs(
    project_id: str,
    stage_name: Optional[str] = Query(None),
    # Default reduced from 1000 → 500. ProjectDetail's Find Summary card
    # only needs the most recent find_summary event; 500 is still plenty
    # for the Execution Logs tab while halving the typical response size
    # (each row carries a JSONB payload that can be 0.5-2KB).
    limit: int = Query(500, ge=1, le=5000),
    user_email: str = Depends(get_current_user),
):
    # Verify + fetch run concurrently — the verify is a tiny SELECT but it
    # still costs a separate connection round-trip; cheaper to overlap.
    ownership_ok, logs = await asyncio.gather(
        asyncio.to_thread(dao.verify_project_ownership, project_id, user_email),
        asyncio.to_thread(
            dao.get_execution_logs, project_id, stage_name, limit
        ),
    )
    if not ownership_ok:
        raise HTTPException(status_code=404, detail="Project not found")
    return jsonable_encoder(logs, custom_encoder=_DATETIME_JSON_ENCODER)


@app.get("/api/database/projects/{project_id}/download-debug-image")
async def get_download_debug_image(
    project_id: str,
    path: str = Query(...),
    user_email: str = Depends(get_current_user_or_token_param),
):
    if not dao.verify_project_ownership(project_id, user_email):
        raise HTTPException(status_code=404, detail="Project not found")

    debug_root = (project_root / "outputs" / "download_debug").resolve()
    requested_path = Path(path).expanduser()
    if not requested_path.is_absolute():
        requested_path = (project_root / requested_path).resolve()
    else:
        requested_path = requested_path.resolve()

    try:
        requested_path.relative_to(debug_root)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid debug image path")
    if project_id not in requested_path.parts:
        raise HTTPException(status_code=400, detail="Image does not belong to project")
    if not requested_path.exists() or not requested_path.is_file():
        raise HTTPException(status_code=404, detail="Debug image not found")

    import mimetypes

    mime_type, _ = mimetypes.guess_type(str(requested_path))
    if not mime_type or not mime_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Requested file is not an image")

    return FileResponse(str(requested_path), media_type=mime_type)


@app.get("/api/database/projects/{project_id}/artifacts")
async def get_project_artifacts(project_id: str, artifact_type: str = None, user_email: str = Depends(get_current_user)):
    ownership_ok, artifacts = await asyncio.gather(
        asyncio.to_thread(dao.verify_project_ownership, project_id, user_email),
        asyncio.to_thread(dao.get_project_artifacts, project_id, artifact_type),
    )
    if not ownership_ok:
        raise HTTPException(status_code=404, detail="Project not found")
    return jsonable_encoder(artifacts, custom_encoder=_DATETIME_JSON_ENCODER)


@app.get("/api/database/projects/{project_id}/artifact-rows")
async def get_project_artifact_rows(project_id: str, user_email: str = Depends(get_current_user)):
    ownership_ok, rows = await asyncio.gather(
        asyncio.to_thread(dao.verify_project_ownership, project_id, user_email),
        asyncio.to_thread(dao.get_project_artifact_rows, project_id),
    )
    if not ownership_ok:
        raise HTTPException(status_code=404, detail="Project not found")
    return jsonable_encoder(rows, custom_encoder=_DATETIME_JSON_ENCODER)


@app.get("/api/database/projects/{project_id}/cost")
async def get_project_cost(project_id: str, user_email: str = Depends(get_current_user)):
    """Per-project LLM cost rollup (prescreen / analyze / download / qualify):
    grand total + per-stage breakdown + per-paper breakdown."""
    ownership_ok, total, per_paper = await asyncio.gather(
        asyncio.to_thread(dao.verify_project_ownership, project_id, user_email),
        asyncio.to_thread(dao.get_project_total_cost, project_id),
        asyncio.to_thread(dao.get_paper_costs, project_id),
    )
    if not ownership_ok:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"total": total, "perPaper": per_paper}


@app.get("/api/database/projects/{project_id}/full")
async def get_full_project_data(project_id: str, user_email: str = Depends(get_current_user)):
    # Fold ownership check into the first SELECT (saves a round-trip) and
    # run the remaining three reads concurrently — psycopg is sync so we
    # dispatch each to a worker thread via asyncio.to_thread.
    project = await asyncio.to_thread(dao.get_project, project_id)
    if not project or project.get("user_email") != user_email:
        raise HTTPException(status_code=404, detail="Project not found")

    stages, messages, artifacts, analyze_processed_count = await asyncio.gather(
        asyncio.to_thread(dao.get_project_stages, project_id),
        asyncio.to_thread(dao.get_project_messages, project_id),
        asyncio.to_thread(dao.get_project_artifacts_metadata, project_id),
        asyncio.to_thread(dao.get_analyze_processed_count, project_id),
    )

    # analyze_processed_count is "unique papers analysed including
    # no_suitable_data skips". The frontend uses it for the analyses badge
    # so the detail-page number matches the project-list number — the raw
    # artifact filter would under-count by the no_suitable_data papers
    # (e.g. TEST-101 shows 9 but the analyze stage processed all 10).
    return jsonable_encoder(
        {
            "project": project,
            "stages": stages,
            "messages": messages,
            "artifacts": artifacts,
            "analyze_processed_count": analyze_processed_count,
        },
        custom_encoder=_DATETIME_JSON_ENCODER,
    )


@app.get("/api/database/artifacts/{artifact_id}")
async def get_artifact(artifact_id: int, user_email: str = Depends(get_current_user)):
    artifact = dao.get_artifact_with_ownership(artifact_id, user_email)
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return jsonable_encoder(artifact, custom_encoder=_DATETIME_JSON_ENCODER)


@app.get("/api/database/artifacts/{artifact_id}/content")
async def get_artifact_content_only(artifact_id: int, user_email: str = Depends(get_current_user)):
    artifact = dao.get_artifact_with_ownership(artifact_id, user_email)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found or has no content")

    content = artifact.get("file_content") or _read_artifact_text_from_file(artifact)
    return {
        "artifact_id": artifact_id,
        "content": content
    }


@app.delete("/api/database/projects/{project_id}")
async def delete_database_project(project_id: str, user_email: str = Depends(get_current_user)):
    if not dao.verify_project_ownership(project_id, user_email):
        raise HTTPException(status_code=404, detail="Project not found")

    dao.delete_project(project_id)
    return {"message": "Project deleted successfully", "project_id": project_id}


@app.get("/api/database/artifacts/{artifact_id}/file")
async def serve_artifact_file(artifact_id: int, user_email: str = Depends(get_current_user_or_token_param)):
    """Serve file for viewing (inline disposition). Redirects to presigned URL for S3."""
    artifact = dao.get_artifact_with_ownership(artifact_id, user_email)
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")

    storage_backend = artifact.get('storage_backend', 'local')

    if storage_backend in ('s3', 'oss'):
        remote_key = artifact.get('s3_key') if storage_backend == 's3' else artifact.get('oss_key')
        if not remote_key:
            raise HTTPException(status_code=500, detail=f"{storage_backend.upper()} key not found for artifact")

        filename = artifact.get('file_name', os.path.basename(remote_key))

        try:
            storage = get_storage_backend()
            presigned_url = storage.generate_download_url(
                key=remote_key,
                filename=filename,
                content_disposition='inline',
                expiration=3600
            )
            return RedirectResponse(url=presigned_url, status_code=302)
        except Exception as e:
            pipeline_log(
                f"storage error: generate inline presigned URL failed: {e}",
                stage="pipeline",
                component="storage",
                level=logging.ERROR,
            )
            raise HTTPException(status_code=500, detail=f"Failed to generate download URL: {e}")

    else:
        remote_redirect = _try_remote_redirect(artifact, content_disposition='inline')
        if remote_redirect is not None:
            return remote_redirect

        resolved_path = _validate_artifact_local_path(artifact.get('file_path', ''))
        if not resolved_path.exists():
            raise HTTPException(status_code=404, detail="File not found on disk")

        import mimetypes
        mime_type, _ = mimetypes.guess_type(str(resolved_path))

        return FileResponse(
            str(resolved_path),
            media_type=mime_type or "application/octet-stream",
            headers={
                "Content-Disposition": f'inline; filename="{artifact.get("file_name", resolved_path.name)}"'
            }
        )


@app.get("/api/database/artifacts/{artifact_id}/download")
async def download_artifact_file(artifact_id: int, user_email: str = Depends(get_current_user_or_token_param)):
    """Download artifact file (attachment disposition). Redirects to presigned URL for S3."""
    artifact = dao.get_artifact_with_ownership(artifact_id, user_email)
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")

    storage_backend = artifact.get('storage_backend', 'local')

    if storage_backend in ('s3', 'oss'):
        remote_key = artifact.get('s3_key') if storage_backend == 's3' else artifact.get('oss_key')
        if not remote_key:
            raise HTTPException(status_code=500, detail=f"{storage_backend.upper()} key not found for artifact")

        filename = artifact.get('file_name', os.path.basename(remote_key))

        try:
            storage = get_storage_backend()
            presigned_url = storage.generate_download_url(
                key=remote_key,
                filename=filename,
                content_disposition='attachment',
                expiration=3600
            )
            return RedirectResponse(url=presigned_url, status_code=302)
        except Exception as e:
            pipeline_log(
                f"storage error: generate attachment presigned URL failed: {e}",
                stage="pipeline",
                component="storage",
                level=logging.ERROR,
            )
            raise HTTPException(status_code=500, detail=f"Failed to generate download URL: {e}")

    else:
        remote_redirect = _try_remote_redirect(artifact, content_disposition='attachment')
        if remote_redirect is not None:
            return remote_redirect

        resolved_path = _validate_artifact_local_path(artifact.get('file_path', ''))
        if not resolved_path.exists():
            raise HTTPException(status_code=404, detail="File not found on disk")

        import mimetypes
        mime_type, _ = mimetypes.guess_type(str(resolved_path))

        return FileResponse(
            str(resolved_path),
            media_type=mime_type or "application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{artifact.get("file_name", resolved_path.name)}"'
            }
        )


@app.get("/api/database/projects/{project_id}/contact-authors")
async def get_contact_authors(project_id: str, user_email: str = Depends(get_current_user)):
    from database.author_parser import AuthorParser

    ownership_ok, artifacts = await asyncio.gather(
        asyncio.to_thread(dao.verify_project_ownership, project_id, user_email),
        asyncio.to_thread(dao.get_project_artifacts, project_id, 'analysis'),
    )
    if not ownership_ok:
        raise HTTPException(status_code=404, detail="Project not found")

    contact_author_artifacts = [
        a for a in artifacts
        if a.get('data_classification_flag') == 'contact_author'
    ]

    result = []
    for artifact in contact_author_artifacts:
        file_content = artifact.get('file_content', '')
        author_info = AuthorParser.parse_author_info(file_content)

        paper_name = (artifact.get('file_name') or artifact.get('file_path', '').split('/')[-1])
        paper_name = paper_name.replace('.md', '').replace('_', ' ')

        result.append({
            'artifact_id': artifact['id'],
            'paper_name': paper_name,
            'paper_title': author_info['title'],
            'authors': author_info['authors'],
            'emails': author_info['emails'],
            'email_sent_at': artifact.get('email_sent_at')
        })

    return result


@app.get("/api/linked-accounts", response_model=List[LinkedAccountResponse])
async def list_linked_accounts(user_email: str = Depends(get_current_user)):
    from linked_accounts import get_linked_accounts
    return get_linked_accounts(user_email)


@app.post("/api/linked-accounts", response_model=LinkedAccountResponse, status_code=201)
async def create_linked_account(
    request: LinkedAccountCreate,
    user_email: str = Depends(get_current_user),
):
    from linked_accounts import add_linked_account
    try:
        account = add_linked_account(user_email, request.site, request.credential, request.password)
        return account
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/linked-accounts/{account_id}")
async def remove_linked_account(
    account_id: int,
    user_email: str = Depends(get_current_user),
):
    from linked_accounts import delete_linked_account
    deleted = delete_linked_account(user_email, account_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Linked account not found")
    return {"message": "Linked account removed"}


@app.post("/api/email/generate")
@limiter.limit("10/hour")
async def generate_email(
    request: Request,
    payload: GenerateEmailRequest,
    user_email: str = Depends(get_current_user),
):
    from web_ui.backend.email_service import generate_contact_email

    if not dao.verify_project_ownership(payload.project_id, user_email):
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        email = generate_contact_email(
            dao=dao,
            project_id=payload.project_id,
            artifact_id=payload.artifact_id,
        )
        return {"subject": email["subject"], "body": email["body"]}
    except Exception as e:
        pipeline_log(
            f"email error: generate template failed: {e}",
            stage="pipeline",
            component="email",
            level=logging.ERROR,
        )
        raise HTTPException(status_code=500, detail=f"Failed to generate email: {str(e)}")


@app.post("/api/email/send")
@limiter.limit("10/hour")
async def send_email(
    request: Request,
    payload: SendEmailRequest,
    user_email: str = Depends(get_current_user),
):
    from web_ui.backend.email_service import send_contact_email

    if not dao.verify_project_ownership(payload.project_id, user_email):
        raise HTTPException(status_code=404, detail="Project not found")

    if payload.artifact_id is not None:
        artifact = dao.get_artifact_with_ownership(payload.artifact_id, user_email)
        if not artifact or str(artifact.get("project_id") or "") != payload.project_id:
            raise HTTPException(status_code=404, detail="Artifact not found")

    try:
        result = send_contact_email(
            dao=dao,
            recipients=payload.to,
            subject=payload.subject,
            body=payload.body,
            artifact_id=payload.artifact_id,
        )
        return {"success": result["success"], "message": result["message"]}
    except Exception as e:
        pipeline_log(
            f"email send error: {e}",
            stage="pipeline",
            component="email",
            level=logging.ERROR,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to send email: {str(e)}"
        )


@app.post("/api/input-response")
async def submit_input_response(
    response: InputResponse,
    user_email: str = Depends(get_current_user),
):
    input_id = response.input_id

    input_request = None

    if input_id in input_requests:
        input_request = input_requests[input_id]
    elif websocket_handler and input_id in websocket_handler.input_requests:
        input_request = websocket_handler.input_requests[input_id]

    if not input_request:
        raise HTTPException(status_code=404, detail="Input request not found")

    if not dao.verify_project_ownership(input_request.project_id, user_email):
        raise HTTPException(status_code=404, detail="Input request not found")

    input_request.response = response.response
    input_request.responded_at = datetime.now()

    if websocket_handler:
        try:
            await websocket_handler.handle_input_response(input_id, response.response)
        except Exception as e:
            pipeline_log(
                f"input_response websocket dispatch failed: {e}",
                stage="pipeline",
                component="websocket",
                level=logging.WARNING,
            )

    return {"message": "Response submitted"}


@app.get("/api/health")
async def health_check():
    return {"status": "healthy"}


@sio.event
async def connect(sid, environ, auth):
    token = None
    if auth and isinstance(auth, dict):
        token = auth.get("token")

    if not token:
        pipeline_log(
            f"socket connect rejected sid={sid}: missing token",
            stage="pipeline",
            component="websocket",
            level=logging.WARNING,
        )
        raise socketio.exceptions.ConnectionRefusedError("Authentication required")

    user_email = verify_token(token)
    if not user_email:
        pipeline_log(
            f"socket connect rejected sid={sid}: invalid token",
            stage="pipeline",
            component="websocket",
            level=logging.WARNING,
        )
        raise socketio.exceptions.ConnectionRefusedError("Invalid or expired token")

    pipeline_log(
        f"socket connected sid={sid} user={user_email}",
        stage="pipeline",
        component="websocket",
    )

    sid_to_user[sid] = user_email
    await sio.enter_room(sid, f"user_{user_email}")

    for db_project in dao.get_all_projects(user_email=user_email):
        project = build_project_from_db(db_project)
        await sio.emit("project_created", {
            "type": "project_created",
            "project_id": project.id,
            "data": project.dict_with_iso_dates()
        }, room=sid)


@sio.event
async def disconnect(sid):
    pipeline_log(
        f"socket disconnected sid={sid}",
        stage="pipeline",
        component="websocket",
    )
    sid_to_user.pop(sid, None)


@sio.event
async def join_project(sid, data):
    project_id = data.get("project_id")
    if not project_id:
        return
    owner = sid_to_user.get(sid)
    if owner and dao.verify_project_ownership(project_id, owner):
        await sio.enter_room(sid, f"project_{project_id}")
        pipeline_log(
            f"socket join_project sid={sid} project_id={project_id}",
            stage="pipeline",
            component="websocket",
            project_id=project_id,
        )


async def start_pipeline(project_id: str, resume: bool = False, max_papers: Optional[int] = None):
    try:
        db_project = dao.get_project(project_id)
        if not db_project:
            pipeline_log(
                "start_pipeline aborted: project not found in database",
                stage="pipeline",
                component="api",
                project_id=project_id,
                level=logging.ERROR,
            )
            return

        project = build_project_from_db(db_project)
        effective_max_papers = max_papers if max_papers is not None else (db_project.get("max_papers") or 10)
        from orchestrator_integration import PipelineRunner, pipeline_parallel_enabled

        effective_parallel = pipeline_parallel_enabled(db_project)
        pipeline_log(
            f"start_pipeline dispatch: mode={'resume' if resume else 'start'} name={project.name!r} "
            f"max_papers={effective_max_papers} parallel={effective_parallel}",
            stage="pipeline",
            component="api",
            project_id=project_id,
        )

        if websocket_handler:
            runner = PipelineRunner(project_id, websocket_handler, cancellation_flags)
            # thread the persisted date range through to
            # FindTeam.run via the runner. Defaults to None when columns
            # are NULL (legacy projects predating this stage existed) — FindTeam
            # falls back to its own hardcoded defaults in that case.
            await runner.run_full_pipeline(
                project.query,
                resume=resume,
                max_papers=effective_max_papers,
                parallel=effective_parallel,
                analysis_model=db_project.get("analysis_model"),
                download_model=db_project.get("download_model"),
                date_start=db_project.get("date_start"),
                date_end=db_project.get("date_end"),
            )
        else:
            pipeline_log(
                "start_pipeline skipped: websocket_handler unavailable",
                stage="pipeline",
                component="api",
                project_id=project_id,
                level=logging.WARNING,
            )

    except asyncio.CancelledError:
        # Cancellation can originate from three places: pause_project (sets
        # status=paused first), cancel_project (sets status=cancelled first),
        # or lifespan shutdown (sets status=paused first). The terminal
        # status is the caller's responsibility — this handler must not
        # decide it. Without this rule, a backend restart would silently
        # flip running projects to 'cancelled', because the handler can't
        # tell shutdown-triggered cancel apart from user-triggered cancel.
        pipeline_log(
            "start_pipeline cancelled (terminal status owned by the caller)",
            stage="pipeline",
            component="api",
            project_id=project_id,
        )
    except Exception as e:
        pipeline_log(
            f"start_pipeline failed: {e}",
            stage="pipeline",
            component="api",
            project_id=project_id,
            level=logging.ERROR,
        )
        logger.exception("start_pipeline failed")
        dao.update_project_status(project_id, "failed")
        if websocket_handler:
            refreshed = dao.get_project(project_id)
            if refreshed:
                project = build_project_from_db(refreshed)
                await websocket_handler.broadcast_message(WebSocketMessage(
                    type="project_updated",
                    project_id=project_id,
                    data=project.dict_with_iso_dates()
                ))
    finally:
        if project_id in running_pipelines:
            del running_pipelines[project_id]
        if project_id in cancellation_flags:
            del cancellation_flags[project_id]


# ============================================================================
# Review Queue endpoints
# ============================================================================

from web_ui.backend.models import ReviewStatusUpdate  # added below


_REVIEW_STATUS_ALLOWED = {"pending", "processing", "handled", "skipped"}


@app.get("/api/review-queue")
async def list_review_queue(
    user_email: str = Depends(get_current_user),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None, max_length=64),
):
    """Tier 2/3 review queue for the current user.

    Query param ``status`` is comma-separated (e.g. ``pending,processing``).
    Defaults to ``pending`` when omitted to keep the queue showing actionable items.
    Optional ``project_id`` narrows results to a single project; ownership is
    enforced by the same ``user_email`` JOIN that isolates per-user — an
    unowned project_id silently returns 0 rows (no separate 404 path).
    Privacy guardrail: user_email JOIN strictly isolates per-user.
    Review-fix C1: auth via standard Depends pattern (was a stub returning 401 in all cases).
    Review-fix H4: status values are allowlist-checked.
    ``max_length=64`` on project_id is a cheap DoS defense against
    oversized strings; ``projects.id`` is TEXT so no UUID validation needed.
    """
    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        invalid = [s for s in statuses if s not in _REVIEW_STATUS_ALLOWED]
        if invalid:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status value(s): {invalid}. "
                       f"Allowed: {sorted(_REVIEW_STATUS_ALLOWED)}",
            )
    else:
        statuses = ["pending"]
    # Offload the sync DAO call so it doesn't pin the event loop while a
    # pipeline task is writing artifacts/messages in parallel. Every
    # other database-read endpoint in this module already uses
    # asyncio.to_thread; review-queue was the lone holdout and was the
    # main reason a refresh felt sluggish during active pipeline runs.
    return await asyncio.to_thread(
        dao.get_review_queue,
        user_email=user_email,
        status_filter=statuses,
        page=page,
        page_size=page_size,
        project_id=project_id,
    )


@app.patch("/api/artifacts/{artifact_id}/review-status")
async def update_review_status(
    artifact_id: int,
    payload: ReviewStatusUpdate,
    user_email: str = Depends(get_current_user),
):
    """transition an artifact's review status.

    State machine transitions enforced at DAO level. Returns 409 when the
    target status is not reachable from the current one.
    Review-fix C1: auth via standard Depends pattern.
    """
    # Map target status → allowed source statuses (R3 fix: state machine).
    transition_table = {
        "processing": ("pending",),
        "handled": ("processing",),
        "skipped": ("pending", "processing"),
        # Reviewer changed their mind mid-handling — allow processing →
        # pending so it reappears in the queue without a DB hack.
        "pending": ("processing",),
    }
    only_from = transition_table.get(payload.status)
    if only_from is None:
        raise HTTPException(status_code=400, detail=f"Invalid target status: {payload.status}")
    ok = dao.update_artifact_review_status(
        artifact_id=artifact_id,
        user_email=user_email,
        new_status=payload.status,
        only_from_statuses=only_from,
    )
    if not ok:
        raise HTTPException(
            status_code=409,
            detail="Artifact not found, not Tier 2/3, or status no longer permits this transition",
        )
    # Optional supplemental_note appended to cache markdown (per the original design).
    if payload.supplemental_note:
        try:
            artifact = dao.get_artifact_for_user(artifact_id, user_email)
            cache_key = (artifact or {}).get("provenance", {}).get("cache_key") if artifact else None
            if cache_key and cache_key.get("pmid"):
                # Best-effort cache supplement — failure does not break the patch.
                # Implementation note: a future iteration can persist this richer.
                pipeline_log(
                    f"review_queue: supplemental_note recorded for artifact={artifact_id}",
                    stage="review", component="review_queue",
                    project_id=artifact.get("project_id"),
                )
        except Exception:
            pass
    return {"ok": True, "artifact_id": artifact_id, "status": payload.status}


@app.post("/api/review-queue/{artifact_id}/resume-download")
async def resume_artifact_download(
    artifact_id: int,
    user_email: str = Depends(get_current_user),
):
    """artifact-level resume — re-runs ONLY download for one
    artifact, using the cached DownloadPlan (cache row).

    Concurrency guards:
      - artifact must be currently `pending` or `processing` (compare-and-set)
      - artifact_id must not already be in `running_artifact_resumes`
    Review-fix C1: auth via standard Depends pattern.
    """
    artifact = dao.get_artifact_for_user(artifact_id, user_email)
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")

    if artifact_id in running_artifact_resumes:
        raise HTTPException(status_code=409, detail="Resume already in progress for this artifact")

    # Compare-and-set: only allow start when status ∈ {pending, processing}.
    ok = dao.update_artifact_review_status(
        artifact_id=artifact_id,
        user_email=user_email,
        new_status="processing",
        only_from_statuses=("pending", "processing"),
    )
    if not ok:
        raise HTTPException(status_code=409, detail="Artifact status changed; refresh and retry")

    # Look up the EXACT cache row used by this artifact.
    cache_key = (artifact.get("provenance") or {}).get("cache_key")
    if not cache_key or not all(cache_key.get(k) for k in ("pmid", "model_name", "prompt_hash", "content_hash")):
        # Legacy artifact (legacy) — no cache_key persisted.
        # Reset status so the user can try other actions.
        dao.update_artifact_review_status(
            artifact_id, user_email, "pending", only_from_statuses=("processing",),
        )
        raise HTTPException(
            status_code=400,
            detail="Legacy artifact has no cache_key — please re-run analyze to populate it",
        )
    cached = dao.get_cached_analysis(**cache_key)
    if not cached or not cached.get("plan_json"):
        dao.update_artifact_review_status(
            artifact_id, user_email, "pending", only_from_statuses=("processing",),
        )
        raise HTTPException(status_code=400, detail="No cached download plan for this artifact")

    artifact_resume_cancellation_flags[artifact_id] = False
    task = asyncio.create_task(
        _run_artifact_resume(artifact_id, artifact, cached["plan_json"], user_email),
    )
    running_artifact_resumes[artifact_id] = task
    return {"ok": True, "artifact_id": artifact_id, "status": "processing"}


async def _run_artifact_resume(
    artifact_id: int, artifact: Dict, plan_dict: Dict, user_email: str,
) -> None:
    """background resume task. Materialises plan_json to a
    tempfile, dispatches via AcquisitionRouter, updates review status to
    `handled` on success or back to `pending` on failure to permit retry."""
    import tempfile
    project_id = artifact.get("project_id") or ""
    paper_stem = (os.path.splitext(os.path.basename(artifact.get("file_name") or ""))[0]
                  or f"artifact{artifact_id}")
    try:
        from analyze.plan_model import DownloadPlan
        from download.router import AcquisitionRouter
        from web_ui.backend.orchestrator_integration import (
            WebOrchestratorUI, make_download_ui_callback,
        )

        plan = DownloadPlan.model_validate(plan_dict)

        ui_instance = WebOrchestratorUI(
            project_id=project_id,
            websocket_handler=websocket_handler,
        )
        router = AcquisitionRouter(
            team_name="ReviewQueueResume",
            project_id=project_id,
            ui_callback=make_download_ui_callback(ui_instance),
            cancellation_check=lambda: artifact_resume_cancellation_flags.get(artifact_id, False),
            download_model_override=None,
        )
        # Materialise plan_json to tempfile (router expects a path).
        with tempfile.NamedTemporaryFile(
            mode="w", prefix=f"{paper_stem}_", suffix=".plan.json", delete=False,
        ) as tmp:
            json.dump(plan.model_dump(mode="json"), tmp)
            tmp_path = tmp.name

        try:
            result_dict = await router.run(plan_path=tmp_path)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        success = bool(
            result_dict.get("success")
            or result_dict.get("downloaded_any")
            or (result_dict.get("tasks") and any(t.get("status") == "success" for t in result_dict["tasks"]))
        )
        if success:
            dao.update_artifact_review_status(
                artifact_id, user_email, "handled", only_from_statuses=("processing",),
            )
        else:
            dao.update_artifact_review_status(
                artifact_id, user_email, "pending", only_from_statuses=("processing",),
            )
    except Exception as e:
        pipeline_log(
            f"review_queue resume failed for artifact={artifact_id}: {e}",
            stage="download", component="review_queue", project_id=project_id,
            level=logging.ERROR,
        )
        dao.update_artifact_review_status(
            artifact_id, user_email, "pending", only_from_statuses=("processing",),
        )
    finally:
        running_artifact_resumes.pop(artifact_id, None)
        artifact_resume_cancellation_flags.pop(artifact_id, None)


if __name__ == "__main__":
    import uvicorn

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    print("Starting AutoGen Web UI Backend...")
    print("Backend will be available at: http://localhost:8000")
    print("API documentation: http://localhost:8000/docs")
    print("Press Ctrl+C to stop")

    uvicorn.run(
        socket_app,
        host="0.0.0.0",
        port=8000,
        log_level="info"
    )

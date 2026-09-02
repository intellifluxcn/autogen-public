"""Centralized OpenRouter configuration helpers."""

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
import openai
from openai import AsyncOpenAI, OpenAI

from utils.pipeline_log import pipeline_log

load_dotenv()

logger = logging.getLogger(__name__)

OPENROUTER_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_DEFAULT_MODEL = "google/gemini-3.1-flash-lite"
OPENROUTER_DEFAULT_DOWNLOAD_MODEL = "anthropic/claude-sonnet-4.6"

# Shared LLM retry/timeout policy. Both analyze and prescreen call
# chat_completion_with_retry() below with their own stage= label, so the
# behaviour stays identical while the log lines carry the right stage.
LLM_CALL_TIMEOUT_SECONDS: float = float(os.getenv("LLM_CALL_TIMEOUT_SECONDS", "180"))
LLM_CALL_MAX_ATTEMPTS: int = int(os.getenv("LLM_CALL_MAX_ATTEMPTS", "3"))
# Backoff schedule applied between attempts (length must be >= max_attempts - 1).
LLM_BACKOFF_SECONDS: Tuple[float, ...] = (1.0, 4.0, 16.0)

# Retryable failures: transient infra/network problems where another attempt
# might succeed. Non-retryable failures (BadRequestError, AuthenticationError,
# PermissionDeniedError, NotFoundError) bail immediately.
_RETRYABLE_OPENAI_ERRORS: Tuple[type, ...] = (
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.RateLimitError,
    openai.InternalServerError,
)
_NON_RETRYABLE_OPENAI_ERRORS: Tuple[type, ...] = (
    openai.BadRequestError,
    openai.AuthenticationError,
    openai.PermissionDeniedError,
    openai.NotFoundError,
)


class LLMCallFailed(RuntimeError):
    """Raised when an LLM call cannot be completed even after retries.

    Carries an `is_retryable` flag describing whether the underlying
    failure was transient (timeout, 5xx, 429) or permanent (bad request,
    auth, context-length overflow).
    """

    def __init__(self, message: str, *, is_retryable: bool = False):
        super().__init__(message)
        self.is_retryable = is_retryable


def _accumulate_openrouter_usage(usage_sink: Dict, response: Any, model: str) -> None:
    """Add one OpenRouter response's usage (tokens + real cost) into ``usage_sink``.
    Cost is present only when the request enabled usage accounting; OpenRouter
    surfaces it as ``response.usage.cost`` (an extra field on the SDK usage)."""
    u = getattr(response, "usage", None)
    if u is None:
        return
    usage_sink["model"] = model
    usage_sink["input_tokens"] = usage_sink.get("input_tokens", 0) + (getattr(u, "prompt_tokens", 0) or 0)
    usage_sink["output_tokens"] = usage_sink.get("output_tokens", 0) + (getattr(u, "completion_tokens", 0) or 0)
    cost = getattr(u, "cost", None)
    if cost is None:
        extra = getattr(u, "model_extra", None) or {}
        cost = extra.get("cost")
    if cost:
        usage_sink["cost_usd"] = usage_sink.get("cost_usd", 0.0) + float(cost)


async def chat_completion_with_retry(
    client: AsyncOpenAI,
    *,
    model: str,
    messages: List[Dict[str, Any]],
    stage: str,
    team: Optional[str] = None,
    project_id: Optional[str] = None,
    timeout: float = LLM_CALL_TIMEOUT_SECONDS,
    max_attempts: int = LLM_CALL_MAX_ATTEMPTS,
    usage_sink: Optional[Dict] = None,
) -> str:
    """Wrap chat.completions.create with timeout and bounded retry.

    Stage-agnostic shared helper: callers pass their own ``stage`` label so
    log lines are attributed correctly (analyze / prescreen / ...). Returns
    the assistant message string (possibly empty). Raises LLMCallFailed when
    all attempts exhaust or the failure is non-retryable; the exception's
    is_retryable flag tells the caller whether the cause was transient.

    When ``usage_sink`` (a dict) is provided, OpenRouter usage accounting is
    enabled and the successful response's tokens + real cost are ACCUMULATED into
    it (keys: model / input_tokens / output_tokens / cost_usd) so a caller can
    sum cost across multiple calls for one paper.
    """
    create_kwargs: Dict[str, Any] = {"model": model, "messages": messages}
    if usage_sink is not None:
        create_kwargs["extra_body"] = {"usage": {"include": True}}
    last_error: Optional[BaseException] = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = await asyncio.wait_for(
                client.chat.completions.create(**create_kwargs),
                timeout=timeout,
            )
            if usage_sink is not None:
                _accumulate_openrouter_usage(usage_sink, response, model)
            return (response.choices[0].message.content or "") if response.choices else ""
        except _NON_RETRYABLE_OPENAI_ERRORS as e:
            pipeline_log(
                f"LLM call non-retryable failure: {type(e).__name__}: {e}",
                stage=stage, team=team, project_id=project_id,
                level=logging.ERROR,
            )
            raise LLMCallFailed(f"{type(e).__name__}: {e}", is_retryable=False) from e
        except _RETRYABLE_OPENAI_ERRORS as e:
            last_error = e
            err_label = f"{type(e).__name__}: {e}"
        except asyncio.TimeoutError as e:
            last_error = e
            err_label = f"asyncio.TimeoutError after {timeout:.0f}s"
        except Exception as e:
            # Unknown error class — treat as retryable by default but log loudly.
            last_error = e
            err_label = f"{type(e).__name__}: {e}"
            pipeline_log(
                f"LLM call unexpected error class (treated as retryable): {err_label}",
                stage=stage, team=team, project_id=project_id,
                level=logging.WARNING,
            )

        if attempt < max_attempts:
            backoff = LLM_BACKOFF_SECONDS[min(attempt - 1, len(LLM_BACKOFF_SECONDS) - 1)]
            pipeline_log(
                f"LLM call attempt {attempt}/{max_attempts} failed ({err_label}); "
                f"retrying in {backoff:.0f}s",
                stage=stage, team=team, project_id=project_id,
                level=logging.WARNING,
            )
            await asyncio.sleep(backoff)
        else:
            pipeline_log(
                f"LLM call attempt {attempt}/{max_attempts} failed ({err_label}); giving up",
                stage=stage, team=team, project_id=project_id,
                level=logging.ERROR,
            )

    raise LLMCallFailed(
        f"All {max_attempts} attempts exhausted; last error: {last_error}",
        is_retryable=True,
    )


def _read_env(name: str) -> str:
    return os.getenv(name, "").strip()


def get_openrouter_api_key() -> str:
    """Return the configured OpenRouter API key."""

    api_key = _read_env("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured in .env or the environment.")
    return api_key


def get_openrouter_base_url() -> str:
    """Return the OpenRouter-compatible base URL."""

    return _read_env("OPENROUTER_BASE_URL") or OPENROUTER_DEFAULT_BASE_URL


def get_openrouter_default_headers() -> Optional[Dict[str, str]]:
    """Return optional OpenRouter headers when configured."""

    headers: Dict[str, str] = {}
    referer = _read_env("OPENROUTER_HTTP_REFERER")
    title = _read_env("OPENROUTER_APP_TITLE")
    if referer:
        headers["HTTP-Referer"] = referer
    if title:
        headers["X-Title"] = title
    return headers or None


def get_openrouter_model(env_name: str, default_model: Optional[str] = None) -> str:
    """Resolve a stage-specific model with fallback to OPENROUTER_MODEL."""

    return (
        _read_env(env_name)
        or _read_env("OPENROUTER_MODEL")
        or default_model
        or OPENROUTER_DEFAULT_MODEL
    )


def get_openrouter_download_model() -> str:
    """Resolve the download-stage model while preserving the legacy Claude default."""

    return (
        _read_env("OPENROUTER_DOWNLOAD_MODEL")
        or _read_env("OPENROUTER_MODEL")
        or OPENROUTER_DEFAULT_DOWNLOAD_MODEL
    )


def build_openrouter_async_client() -> AsyncOpenAI:
    """Create an async OpenAI-compatible client pointed at OpenRouter."""

    return AsyncOpenAI(
        api_key=get_openrouter_api_key(),
        base_url=get_openrouter_base_url(),
        default_headers=get_openrouter_default_headers(),
    )


def build_openrouter_sync_client() -> OpenAI:
    """Create a sync OpenAI-compatible client pointed at OpenRouter."""

    return OpenAI(
        api_key=get_openrouter_api_key(),
        base_url=get_openrouter_base_url(),
        default_headers=get_openrouter_default_headers(),
    )

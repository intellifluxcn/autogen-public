"""Filter helper for OpenRouter model IDs.

Used by the /api/models endpoint and by CreateProjectRequest validation
to restrict the model list to mainstream Google / OpenAI / Anthropic models.
"""

from __future__ import annotations

_ALLOWED_PREFIXES = ("google/", "openai/", "anthropic/")


def is_allowed_model(model_id: str) -> bool:
    """Return True if *model_id* starts with an allowed provider prefix.

    Provider prefixes are ``google/``, ``openai/``, and ``anthropic/``.
    Previously this filter also rejected ``-preview`` / ``-experimental`` /
    ``-beta`` / ``-instruct`` segments, but that excluded current
    cutting-edge models (notably ``google/gemini-3.1-pro-preview``) whose
    ``-preview`` tag is just OpenRouter's release-stage marker, not an
    indication of instability or surprise pricing. Operators who don't
    want preview models surfaced in the picker can curate via product UX
    rather than have the backend hide them.
    """
    if not model_id:
        return False
    return any(model_id.startswith(prefix) for prefix in _ALLOWED_PREFIXES)

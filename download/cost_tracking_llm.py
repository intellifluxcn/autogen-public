"""browser-use ChatOpenAI that captures the real per-run OpenRouter cost.

browser-use's own usage path records tokens but drops the ``cost`` field
OpenRouter returns. This subclass enables OpenRouter usage accounting on every
request and accumulates the real cost + tokens of each browser-use LLM step into
a per-instance meter, so the download stage can record one paper's total cost.
"""

from __future__ import annotations

from typing import Any, Dict

from browser_use import ChatOpenAI


class _CostMeter:
    def __init__(self) -> None:
        self.model: Any = None
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost_usd = 0.0
        self.calls = 0

    def record(self, response: Any, model: str) -> None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        self.calls += 1
        self.model = model
        self.input_tokens += getattr(usage, "prompt_tokens", 0) or 0
        self.output_tokens += getattr(usage, "completion_tokens", 0) or 0
        cost = getattr(usage, "cost", None)
        if cost is None:
            extra = getattr(usage, "model_extra", None) or {}
            cost = extra.get("cost")
        if cost:
            self.cost_usd += float(cost)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": self.cost_usd,
        }


class CostTrackingChatOpenAI(ChatOpenAI):
    """ChatOpenAI variant that meters real OpenRouter cost across all calls of one
    agent run. ``get_client()`` builds a fresh AsyncOpenAI per call (browser-use
    behaviour), so we patch each client's ``create`` to (1) request usage
    accounting and (2) feed the response into the shared per-instance meter."""

    @property
    def cost_meter(self) -> _CostMeter:
        meter = self.__dict__.get("_cost_meter")
        if meter is None:
            meter = self.__dict__["_cost_meter"] = _CostMeter()
        return meter

    def get_client(self):
        client = super().get_client()
        meter = self.cost_meter
        model = self.model
        orig_create = client.chat.completions.create

        async def create(*args, **kwargs):
            extra_body = dict(kwargs.get("extra_body") or {})
            extra_body.setdefault("usage", {"include": True})
            kwargs["extra_body"] = extra_body
            response = await orig_create(*args, **kwargs)
            try:
                meter.record(response, model)
            except Exception:
                pass
            return response

        client.chat.completions.create = create
        return client

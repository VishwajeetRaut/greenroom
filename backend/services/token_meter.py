"""
Real token accounting for every LLM call.

Why this exists
---------------
docs/EVALUATION_METRICS.md §7 lists "Cost per completed session" under
*not yet measurable*, with the reason: "no token-usage tracking wired up."
This module is that wiring. Nothing here estimates: every number comes from
the provider's own reported usage (Groq returns ``usage_metadata`` on the
message and ``token_usage`` in ``response_metadata``; the OpenAI-compatible
fallback returns a ``usage`` object), so a cost figure derived from it is a
real measurement rather than a chars/4 approximation.

How it attaches
---------------
Every Groq call in this codebase is constructed through
``services.llm._make_llm``, so the recorder is attached there as a LangChain
callback rather than at each call site. That matters because several call
sites are LCEL chains ending in an output parser
(``ChatPromptTemplate | ChatGroq | StrOutputParser``) — by the time the chain
returns, the ``AIMessage`` carrying the usage metadata has already been
discarded. A callback fires at ``on_llm_end``, before the parser runs, so it
sees usage on the chain paths and the plain ``.invoke()`` paths alike.

The Ollama-cloud fallback doesn't go through LangChain at all (it's a raw
httpx POST), so that path records via ``record_openai_usage`` instead.

Attribution
-----------
Usage is tagged with a ``call_site`` — "next_question", "evaluate_session",
"harness_generator", and so on — because the interesting question is not
"how many tokens did we spend" but "which part of a session is expensive."
The answer drives which call gets a cheaper model, a cache, or a smaller
prompt.
"""

from __future__ import annotations

import os
import threading
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from services.logger import log

# ── pricing ──────────────────────────────────────────────────────────────────
#
# USD per 1M tokens, on-demand (non-batch, uncached) rates.
#
# SOURCE: third-party pricing aggregators (cloudzero.com, aipricing.guru,
# eesel.ai), retrieved 2026-08-06. Groq's own pricing page renders its table
# client-side and console.groq.com/docs/pricing 404s, so these could not be
# machine-verified against the vendor directly.
#
# TREAT AS INDICATIVE, NOT AUTHORITATIVE: verify against the Groq console
# before using these for budgeting or for a spend commitment. The token
# COUNTS this module reports are exact (provider-reported); only the $/token
# multiplier below carries this caveat.
#
# Two discounts apply on top and are NOT modelled here, so real spend should
# come in at or below what this reports:
#   - Batch API: flat 50% off synchronous pricing.
#   - Cached input tokens: flat 50% off on a Groq-side cache hit.
#     (Independent of services.llm_cache, which avoids the call entirely.)
PRICING_RETRIEVED = "2026-08-06"

PRICING: dict[str, tuple[float, float]] = {
    # model id: (input $/1M, output $/1M)
    "llama-3.3-70b-versatile": (0.59, 0.79),
    "llama-3.1-8b-instant":    (0.05, 0.08),
    "openai/gpt-oss-120b":     (0.15, 0.60),
    "openai/gpt-oss-20b":      (0.075, 0.30),
    "qwen/qwen3.6-27b":        (0.60, 3.00),
}


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Returns None for a model with no known price rather than silently
    reporting $0 — an unpriced model must not look free in a cost matrix."""
    price = PRICING.get(model)
    if price is None:
        return None
    in_rate, out_rate = price
    return (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000


# ── in-process accumulator ───────────────────────────────────────────────────

METER_ENABLED = os.environ.get("LLM_METER_ENABLED", "true").lower() == "true"


class _Meter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: dict[tuple[str, str, str], dict] = {}  # (call_site, provider, model) -> totals

    def add(
        self, call_site: str, provider: str, model: str,
        input_tokens: int, output_tokens: int,
    ) -> None:
        key = (call_site, provider, model)
        with self._lock:
            row = self._rows.setdefault(key, {"calls": 0, "input_tokens": 0, "output_tokens": 0})
            row["calls"] += 1
            row["input_tokens"] += input_tokens
            row["output_tokens"] += output_tokens

    def snapshot(self) -> dict:
        with self._lock:
            rows = [
                {
                    "call_site": call_site,
                    "provider": provider,
                    "model": model,
                    **totals,
                    "cost_usd": cost_usd(model, totals["input_tokens"], totals["output_tokens"]),
                }
                for (call_site, provider, model), totals in self._rows.items()
            ]
        rows.sort(key=lambda r: r["input_tokens"] + r["output_tokens"], reverse=True)
        priced = [r["cost_usd"] for r in rows if r["cost_usd"] is not None]
        return {
            "enabled": METER_ENABLED,
            "pricing_retrieved": PRICING_RETRIEVED,
            "pricing_is_indicative": True,
            "total_calls": sum(r["calls"] for r in rows),
            "total_input_tokens": sum(r["input_tokens"] for r in rows),
            "total_output_tokens": sum(r["output_tokens"] for r in rows),
            "total_cost_usd": round(sum(priced), 6) if priced else 0.0,
            "unpriced_models": sorted({r["model"] for r in rows if r["cost_usd"] is None}),
            "by_call_site": rows,
        }

    def clear(self) -> None:
        with self._lock:
            self._rows.clear()


_meter = _Meter()


# ── recording ────────────────────────────────────────────────────────────────

def record(
    call_site: str, provider: str, model: str,
    input_tokens: int, output_tokens: int, **extra: Any,
) -> None:
    if not METER_ENABLED:
        return
    _meter.add(call_site, provider, model, input_tokens, output_tokens)
    log.info(
        "llm.usage",
        call_site=call_site,
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd(model, input_tokens, output_tokens),
        **extra,
    )


def record_openai_usage(call_site: str, provider: str, response_json: dict) -> None:
    """Records from a raw OpenAI-compatible ``/chat/completions`` response —
    the Ollama-cloud fallback path, which never touches LangChain. Silently
    does nothing if the provider omitted ``usage``: a missing count must not
    break a request that otherwise succeeded."""
    usage = (response_json or {}).get("usage") or {}
    input_tokens = usage.get("prompt_tokens")
    output_tokens = usage.get("completion_tokens")
    if input_tokens is None and output_tokens is None:
        return
    record(
        call_site, provider,
        response_json.get("model") or "unknown",
        int(input_tokens or 0), int(output_tokens or 0),
    )


def _usage_from_llm_result(response: Any) -> tuple[int, int, str | None] | None:
    """Pulls (input, output, model) out of a LangChain ``LLMResult``.

    Tries ``llm_output["token_usage"]`` first (what ChatGroq populates), then
    the message's own ``usage_metadata``. Both are checked because which one
    is present varies by integration and version, and a silently-zero token
    count is worse than no count at all.
    """
    llm_output = getattr(response, "llm_output", None) or {}
    model = llm_output.get("model_name")
    usage = llm_output.get("token_usage") or {}
    if usage.get("prompt_tokens") is not None or usage.get("completion_tokens") is not None:
        return int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0), model

    for generation_list in getattr(response, "generations", None) or []:
        for generation in generation_list:
            message = getattr(generation, "message", None)
            meta = getattr(message, "usage_metadata", None) or {}
            if meta:
                model = model or (getattr(message, "response_metadata", None) or {}).get("model_name")
                return int(meta.get("input_tokens") or 0), int(meta.get("output_tokens") or 0), model
    return None


class UsageRecorder(BaseCallbackHandler):
    """Attach to a chat model to record usage for everything it runs.

    Attached per-model-instance in ``services.llm._make_llm`` (which builds a
    fresh ChatGroq per call), so one recorder never sees another call site's
    traffic.
    """

    def __init__(self, call_site: str, provider: str = "groq", model: str | None = None):
        self.call_site = call_site
        self.provider = provider
        self.model = model

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        try:
            usage = _usage_from_llm_result(response)
            if usage is None:
                return
            input_tokens, output_tokens, model = usage
            record(self.call_site, self.provider, model or self.model or "unknown",
                   input_tokens, output_tokens)
        except Exception as exc:  # noqa: BLE001 - metering must never break a call
            log.warning("llm.usage.record_failed", call_site=self.call_site, error=str(exc))


# ── public accessors ─────────────────────────────────────────────────────────

def stats() -> dict:
    return _meter.snapshot()


def clear() -> None:
    _meter.clear()

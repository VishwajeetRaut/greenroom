"""
Prometheus metrics for Greenroom.

What this closes
----------------
docs/EVALUATION_METRICS.md §6 and §7 list five things as *not yet measurable*.
Task #2 (token accounting) closed "cost per completed session". This module
closes the other four, all of which failed for the same reason: the data
existed, but only as a line of JSON on stdout that nothing aggregated.

  * **P50/P95 latency** — "logs latency per request to stdout only; nothing is
    persisted anywhere aggregatable yet" → `greenroom_http_request_duration_seconds`
  * **LLM fallback rate (Groq → Ollama)** — "not currently logged as a
    countable event" → `greenroom_llm_calls_total{provider}` and
    `greenroom_llm_fallback_total`
  * **Piston vs Wandbox split** — "logged per-request but not aggregated
    anywhere queryable" → `greenroom_sandbox_runs_total{backend}`
  * **Guardrail trigger rate** (§6) — "needs a logged trigger event, which
    doesn't exist yet" → `greenroom_guardrail_checks_total{layer,result}`

Cardinality
-----------
The one way a metrics layer takes down the thing it is monitoring is unbounded
label cardinality. Two rules here, both enforced rather than assumed:

  * HTTP paths are recorded as the *route template*
    (`/api/interview/{session_id}/resume`), never the raw path — otherwise
    every session UUID becomes its own time series and the registry grows
    without bound. `_route_template` falls back to "unmatched" rather than to
    the raw path, because a 404 flood on random URLs is exactly when you least
    want to be minting new series.
  * Every other label is drawn from a small closed set (track, provider,
    backend, outcome). Nothing user-supplied is ever a label value.

Metrics never break a request. Every helper swallows its own exceptions —
a monitoring bug must not become an outage.
"""

from __future__ import annotations

import os

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest

from services.logger import log

METRICS_ENABLED = os.environ.get("METRICS_ENABLED", "true").lower() == "true"

# A dedicated registry rather than the global default: it keeps the process's
# own gc/platform collectors out of the endpoint, and lets tests build a clean
# registry without leaking state between them.
REGISTRY = CollectorRegistry()

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


# ── HTTP ─────────────────────────────────────────────────────────────────────

http_requests = Counter(
    "greenroom_http_requests_total",
    "HTTP requests served, by route template and status class.",
    ["method", "route", "status"],
    registry=REGISTRY,
)

http_duration = Histogram(
    "greenroom_http_request_duration_seconds",
    "End-to-end request latency.",
    ["method", "route"],
    # Tuned to this app: an interview turn is an LLM round-trip (~0.5-2s), a
    # code run is a sandbox round-trip (~1-10s), and an evaluation can take
    # 20s+. The default buckets top out at 10s and would put every evaluation
    # in +Inf, making the P95 that matters most unreadable.
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30, 60),
    registry=REGISTRY,
)


# ── LLM ──────────────────────────────────────────────────────────────────────

llm_calls = Counter(
    "greenroom_llm_calls_total",
    "LLM calls, by call site and provider.",
    ["call_site", "provider", "model"],
    registry=REGISTRY,
)

llm_tokens = Counter(
    "greenroom_llm_tokens_total",
    "Provider-reported tokens.",
    ["call_site", "provider", "direction"],
    registry=REGISTRY,
)

llm_cost = Counter(
    "greenroom_llm_cost_usd_total",
    "Estimated spend. Token counts are exact; the $/token rate is indicative "
    "(see services.token_meter.PRICING).",
    ["call_site", "model"],
    registry=REGISTRY,
)

llm_fallback = Counter(
    "greenroom_llm_fallback_total",
    "Times the primary provider failed and the request was re-issued against "
    "the fallback. The Groq→Ollama rate EVALUATION_METRICS.md §7 wanted.",
    ["call_site"],
    registry=REGISTRY,
)

llm_cache_lookups = Counter(
    "greenroom_llm_cache_lookups_total",
    "LLM response cache hits and misses.",
    ["namespace", "result"],
    registry=REGISTRY,
)


# ── Guardrail ────────────────────────────────────────────────────────────────

guardrail_checks = Counter(
    "greenroom_guardrail_checks_total",
    "Guardrail evaluations by layer and outcome. 'triggered' means the layer "
    "caught a leak and the response was regenerated or replaced.",
    ["track", "layer", "result"],
    registry=REGISTRY,
)


# ── Sandbox ──────────────────────────────────────────────────────────────────

sandbox_runs = Counter(
    "greenroom_sandbox_runs_total",
    "Code executions by language and which backend actually served them.",
    ["language", "backend", "outcome"],
    registry=REGISTRY,
)

sandbox_duration = Histogram(
    "greenroom_sandbox_duration_seconds",
    "Sandbox execution latency.",
    ["backend"],
    buckets=(0.25, 0.5, 1, 2, 5, 10, 20, 30, 60),
    registry=REGISTRY,
)


# ── Sessions and evaluations ─────────────────────────────────────────────────

sessions = Counter(
    "greenroom_sessions_total",
    "Sessions by track and how they ended.",
    ["track", "outcome"],
    registry=REGISTRY,
)

evaluations = Counter(
    "greenroom_evaluations_total",
    "Evaluations by which path produced them. 'defaulted' means both providers "
    "failed and the candidate got the placeholder report — the number to alert on.",
    ["track", "path"],
    registry=REGISTRY,
)


# ── recording helpers ────────────────────────────────────────────────────────
#
# Every helper is a no-op when disabled and swallows its own exceptions: a bug
# in monitoring must never surface as a failed interview.

def _safe(fn):
    def wrapper(*args, **kwargs):
        if not METRICS_ENABLED:
            return
        try:
            fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            log.warning("metrics.record_failed", metric=fn.__name__, error=str(exc)[:200])
    return wrapper


def route_template(request) -> str:
    """The matched route's path template, so session UUIDs never become labels.

    Falls back to "unmatched" rather than the raw path: a 404 flood against
    random URLs is precisely when minting a new time series per URL would hurt.
    """
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    return template or "unmatched"


@_safe
def record_http(method: str, route: str, status: int, duration_seconds: float) -> None:
    http_requests.labels(method=method, route=route, status=str(status)).inc()
    http_duration.labels(method=method, route=route).observe(duration_seconds)


@_safe
def record_llm(call_site: str, provider: str, model: str,
               input_tokens: int, output_tokens: int, cost_usd: float | None) -> None:
    llm_calls.labels(call_site=call_site, provider=provider, model=model).inc()
    llm_tokens.labels(call_site=call_site, provider=provider, direction="input").inc(input_tokens)
    llm_tokens.labels(call_site=call_site, provider=provider, direction="output").inc(output_tokens)
    if cost_usd:
        llm_cost.labels(call_site=call_site, model=model).inc(cost_usd)
    if provider == "fallback":
        llm_fallback.labels(call_site=call_site).inc()


@_safe
def record_cache(namespace: str, hit: bool) -> None:
    llm_cache_lookups.labels(namespace=namespace, result="hit" if hit else "miss").inc()


@_safe
def record_guardrail(track: str, layer: str, triggered: bool) -> None:
    guardrail_checks.labels(
        track=track, layer=layer, result="triggered" if triggered else "clean",
    ).inc()


@_safe
def record_sandbox(language: str, backend: str, ok: bool, duration_seconds: float) -> None:
    sandbox_runs.labels(language=language, backend=backend, outcome="ok" if ok else "error").inc()
    sandbox_duration.labels(backend=backend).observe(duration_seconds)


@_safe
def record_session(track: str, outcome: str) -> None:
    sessions.labels(track=track, outcome=outcome).inc()


@_safe
def record_evaluation(track: str, path: str) -> None:
    evaluations.labels(track=track, path=path).inc()


def render() -> bytes:
    """Prometheus text exposition for the /metrics endpoint."""
    return generate_latest(REGISTRY)

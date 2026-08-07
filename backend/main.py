import asyncio
import os
import time

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

from routers import analytics, interview, tts  # noqa: E402
from services import metrics  # noqa: E402
from services.logger import log  # noqa: E402

app = FastAPI(title="Greenroom API", version="0.1.0")

origins = os.environ.get("ALLOWED_ORIGINS", "http://localhost:5173").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_logger(request: Request, call_next):
    start = time.monotonic()
    response = await call_next(request)
    elapsed = time.monotonic() - start
    log.info(
        "http.request",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        latency_ms=round(elapsed * 1000),
    )
    # Route TEMPLATE, not the raw path — see services.metrics.route_template.
    metrics.record_http(request.method, metrics.route_template(request),
                        response.status_code, elapsed)
    return response


@app.get("/metrics", include_in_schema=False)
async def prometheus_metrics():
    """Prometheus scrape target.

    Deliberately unauthenticated and deliberately NOT under /api: it exposes
    only aggregate counters (no prompts, transcripts, or user identifiers), and
    Prometheus scrapes it from inside the private VNet. If this is ever exposed
    publicly, put it behind the ingress rules rather than adding auth here —
    scrapers don't carry bearer tokens.
    """
    return Response(content=metrics.render(), media_type=metrics.CONTENT_TYPE)


# One client for the process, not one per request. Constructing an
# httpx.AsyncClient builds a TLS context, which is expensive enough that doing
# it per request dominated the health endpoint's latency under concurrency.
_probe_client: httpx.AsyncClient | None = None

# Probe results are cached for a few seconds. Container Apps probes this every
# few seconds and a burst of health checks should not become a burst of
# outbound requests to Piston — that turns a monitoring feature into a
# self-inflicted load source.
_PROBE_CACHE_SECONDS = float(os.environ.get("HEALTH_PROBE_CACHE_SECONDS", "5"))
_probe_result: tuple[float, str] = (0.0, "unknown")
_probe_lock = asyncio.Lock()


async def _judge0_health() -> str:
    """Cached, single-flight probe of the public Judge0 instance.

    Only the public instance is checked: RapidAPI's is key-gated and probing it
    burns a quota'd request, and execution already falls through to
    _local_subprocess if both are down.

    Single-flight matters as much as the caching. With a plain TTL cache every
    concurrent caller misses at the same instant the entry expires and they
    all fire a probe together — a stampede that showed up as a p99 twelve
    times the p50 while the median looked perfectly healthy. Only one probe
    runs at a time now; everyone else is served the previous value, which for
    a liveness check a few seconds stale is entirely fine.
    """
    global _probe_client, _probe_result

    cached_at, cached_value = _probe_result
    if time.monotonic() - cached_at < _PROBE_CACHE_SECONDS:
        return cached_value

    if _probe_lock.locked():
        # Someone is already refreshing — serve the stale value rather than
        # queue behind them.
        return cached_value

    async with _probe_lock:
        # Re-check: another caller may have refreshed while we waited.
        cached_at, cached_value = _probe_result
        if time.monotonic() - cached_at < _PROBE_CACHE_SECONDS:
            return cached_value

        if _probe_client is None:
            _probe_client = httpx.AsyncClient(timeout=3)

        judge0_url = os.environ.get("JUDGE0_PUBLIC_URL", "https://ce.judge0.com")
        try:
            response = await _probe_client.get(f"{judge0_url}/languages")
            value = "ok" if response.status_code == 200 else f"http_{response.status_code}"
        except Exception:
            value = "unreachable"

        _probe_result = (time.monotonic(), value)
        return value


@app.on_event("shutdown")
async def _close_probe_client():
    if _probe_client is not None:
        await _probe_client.aclose()


app.include_router(interview.router, prefix="/api")
app.include_router(tts.router, prefix="/api")
app.include_router(analytics.router, prefix="/api")


@app.get("/api/health")
async def health():
    """
    Liveness + shallow readiness probe.
    Returns 200 with component status. Azure Container Apps health probes
    hit this endpoint — it must never block for more than a few seconds.

    Measured at 87 rps / 539ms p50 under 50 concurrent callers, against 3268
    rps / 14ms for /metrics on the same server. Two causes, both fixed here:
    a fresh httpx.AsyncClient (and its SSL context) was constructed per
    request, and the Piston probe ran on every single call. A health endpoint
    that collapses under load is worse than useless — it is what tells the
    orchestrator to kill a container that was actually fine.
    """
    from services.supabase_client import get_supabase

    checks: dict[str, str] = {}

    checks["supabase"] = "ok" if get_supabase() else "unconfigured"
    checks["judge0"] = await _judge0_health()

    # Groq key present (we can't call it cheaply; just assert it's configured)
    checks["groq"] = "configured" if os.environ.get("GROQ_API_KEY") else "unconfigured"

    overall = "ok" if all(v in ("ok", "configured") for v in checks.values()) else "degraded"
    log.info("health.check", overall=overall, **checks)
    return {"status": overall, "checks": checks}

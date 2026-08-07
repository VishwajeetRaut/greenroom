import os
import time

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


app.include_router(interview.router, prefix="/api")
app.include_router(tts.router, prefix="/api")
app.include_router(analytics.router, prefix="/api")


@app.get("/api/health")
async def health():
    """
    Liveness + shallow readiness probe.
    Returns 200 with component status. Azure Container Apps health probes
    hit this endpoint — it must never block for more than a few seconds.
    """
    import httpx

    from services.supabase_client import get_supabase

    checks: dict[str, str] = {}

    # Supabase reachability (lightweight — just checks the client is configured)
    sb = get_supabase()
    checks["supabase"] = "ok" if sb else "unconfigured"

    # Judge0 reachability (fire-and-forget, 3 s timeout). Checks the public
    # instance only — RapidAPI's key-gated instance isn't cheap to probe
    # without burning a quota'd request, and the code path already falls
    # back to local subprocess execution if both are down.
    judge0_url = os.environ.get("JUDGE0_PUBLIC_URL", "https://ce.judge0.com")
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get(f"{judge0_url}/languages")
            checks["judge0"] = "ok" if r.status_code == 200 else f"http_{r.status_code}"
    except Exception:
        checks["judge0"] = "unreachable"

    # Groq key present (we can't call it cheaply; just assert it's configured)
    checks["groq"] = "configured" if os.environ.get("GROQ_API_KEY") else "unconfigured"

    overall = "ok" if all(v in ("ok", "configured") for v in checks.values()) else "degraded"
    log.info("health.check", overall=overall, **checks)
    return {"status": overall, "checks": checks}

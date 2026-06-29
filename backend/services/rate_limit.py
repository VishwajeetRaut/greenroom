"""
Lightweight in-memory rate limiter — protects the LLM keys (Groq/Ollama) from
abuse or runaway client bugs without adding a new infrastructure dependency.

Known limitation: counters are per-process, so with multiple backend replicas
(see DEPLOYMENT.md — backend currently allows up to 2) each replica enforces
its own limit independently, meaning the *effective* ceiling across the fleet
is (limit × replica count). That's an acceptable trade-off for a POC — the
goal here is "stop one client from hammering the API," not hard multi-tenant
quota enforcement. True fleet-wide limiting needs a shared store (Redis
INCR + EXPIRE is the standard pattern) — noted here for when that's needed.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException, status

_WINDOW_SECONDS = 60
_buckets: dict[str, deque] = defaultdict(deque)
_lock = Lock()


def check_rate_limit(key: str, max_per_minute: int = 30) -> None:
    """Raises HTTP 429 if `key` (typically a user id) has exceeded max_per_minute
    requests in the trailing 60 seconds."""
    now = time.monotonic()
    with _lock:
        bucket = _buckets[key]
        while bucket and now - bucket[0] > _WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) >= max_per_minute:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests — please slow down and try again shortly.",
            )
        bucket.append(now)

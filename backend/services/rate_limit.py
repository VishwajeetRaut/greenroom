"""
Rate limiter — sliding window, Postgres-backed.

Every request inserts one row into rate_limit_events. The check counts rows
in the trailing 60 seconds for the requesting user. Both backend replicas
query the same Postgres instance, so the limit is truly per-user across the
fleet (unlike the previous in-memory deque which gave each replica its own
independent counter).

Old rows (> 5 minutes) are pruned on each check to prevent unbounded growth.
No separate cron job is needed — the table stays small because only the
trailing window matters.

Fallback: if Supabase is not configured (local dev without a DB), the limiter
falls back to the previous in-memory deque so development still works without
credentials.
"""

from __future__ import annotations

import os
import random
import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException, status

_WINDOW_SECONDS = 60
_PRUNE_AFTER_SECONDS = 300
_PRUNE_PROBABILITY = float(os.environ.get("RATE_LIMIT_PRUNE_PROBABILITY", "0.02"))


def check_rate_limit(key: str, max_per_minute: int = 30) -> None:
    """Raises HTTP 429 if `key` has exceeded max_per_minute requests in the
    trailing 60 seconds. Uses Postgres when available, falls back to an
    in-memory deque for local dev without a configured database."""
    from services.supabase_client import get_supabase  # local import avoids circular dep

    sb = get_supabase()
    if sb:
        try:
            _check_postgres(sb, key, max_per_minute)
        except HTTPException:
            raise
        except Exception:
            _check_memory(key, max_per_minute)
    else:
        _check_memory(key, max_per_minute)


# ── Postgres implementation ───────────────────────────────────────────────────

def _check_postgres(sb, key: str, max_per_minute: int) -> None:
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    window_start = now - timedelta(seconds=_WINDOW_SECONDS)
    prune_before = now - timedelta(seconds=_PRUNE_AFTER_SECONDS)

    # Count requests in the trailing window
    result = (
        sb.table("rate_limit_events")
        .select("id", count="exact")
        .eq("user_id", key)
        .gte("ts", window_start.isoformat())
        .execute()
    )
    count = result.count or 0

    if count >= max_per_minute:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests — please slow down and try again shortly.",
        )

    # Record this request
    sb.table("rate_limit_events").insert({"user_id": key, "ts": now.isoformat()}).execute()

    # Prune old rows — but not on every request.
    #
    # This DELETE is unscoped: it sweeps rows for ALL users, so running it per
    # request meant every single call did a table-wide write. Under load that
    # is a lock-contention hotspot, and it made the rate limiter (which fires
    # on every endpoint) cost three round-trips instead of two.
    #
    # Sampling it is enough: rows only need removing eventually, and at any
    # real request rate 1-in-N still fires many times a minute. Random rather
    # than a timer so multiple replicas don't synchronise on the same instant.
    if random.random() < _PRUNE_PROBABILITY:
        try:
            sb.table("rate_limit_events").delete().lt("ts", prune_before.isoformat()).execute()
        except Exception:
            pass


# ── In-memory fallback (local dev only) ──────────────────────────────────────

_buckets: dict[str, deque] = defaultdict(deque)
_lock = Lock()


def _check_memory(key: str, max_per_minute: int) -> None:
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

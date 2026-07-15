"""
Rate limiter — sliding window, Postgres-backed.

The count-and-record step runs as a single atomic call to the check_rate_limit
Postgres function (see supabase/migrations/20260715_rate_limit_atomic.sql).
It must stay one call: the previous version counted rows and then inserted in
a separate round-trip, which let a concurrent burst all read a count below the
limit before any insert landed, admitting every request. Both backend replicas
call the same function, so the limit is per-user across the fleet.

Old rows (> 5 minutes) are pruned after each check to prevent unbounded growth.
No separate cron job is needed — the table stays small because only the
trailing window matters.

Fallback: if Supabase is not configured (local dev without a DB), the limiter
falls back to an in-memory deque so development still works without
credentials. That path is per-replica and therefore only correct for a single
process — it is not a substitute for the Postgres path in a deployment.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException, status

_WINDOW_SECONDS = 60
_PRUNE_AFTER_SECONDS = 300


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
    # One call: the function counts the window and records the request while
    # holding a per-user advisory lock. Splitting this back into a count and a
    # separate insert reintroduces the race the function exists to close.
    result = sb.rpc(
        "check_rate_limit",
        {"p_user_id": key, "p_max": max_per_minute, "p_window_seconds": _WINDOW_SECONDS},
    ).execute()

    allowed = result.data
    if isinstance(allowed, list):  # some client versions wrap scalar returns
        allowed = allowed[0] if allowed else None

    if allowed is False:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests — please slow down and try again shortly.",
        )

    _prune(sb)


def _prune(sb) -> None:
    """Drop rows outside the retention window. Fire-and-forget: a failed prune
    only costs table size, so it must never fail a request that the limiter
    already admitted."""
    from datetime import datetime, timedelta, timezone

    prune_before = datetime.now(timezone.utc) - timedelta(seconds=_PRUNE_AFTER_SECONDS)
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

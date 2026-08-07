"""
Short-lived cache for validated JWTs.

Why
---
`auth.get_current_user` validated every single request by calling
`supabase.auth.get_user(token)` — a network round-trip to GoTrue, per request,
with no caching. Measured under load: an authenticated request cost ~312ms
before doing any actual work.

Worse, the retry that guards against a transient Supabase blip used a
**blocking** `time.sleep(0.3)`, inside a dependency FastAPI runs in its
threadpool. That pool defaults to 40 workers and is shared with every
`run_in_threadpool` call in the app — LLM calls, persistence, question
selection. So roughly 133 failed auths per second saturates it completely and
stalls every in-flight interview. A candidate's token expiring, a frontend
retry loop, or a Supabase blip (exactly when the retry fires) is enough to
trigger it.

Design notes
------------
Keyed on a **SHA-256 of the token**, never the token itself: this dict would
otherwise be a bag of live credentials sitting in memory, readable from any
traceback or heap dump.

Entries are capped at the JWT's own `exp` claim. The claim is read without
verifying the signature — which is safe *only* because it is used to make the
cache expire EARLIER, never to authorise anything. Supabase remains the sole
authority on whether a token is valid; this just refuses to trust its answer
for longer than the token itself would live.

Failures are cached too, briefly. A retry storm with one bad token otherwise
repeats the full round-trip every time, which is the exact scenario that
starves the threadpool.

The staleness this introduces is bounded and deliberate: a token revoked
server-side keeps working for up to AUTH_CACHE_TTL_SECONDS (default 60). For a
mock-interview app that trade is clearly worth it; if it ever isn't, set the
TTL to 0 to disable.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import threading
import time
from typing import Any

TTL_SECONDS = int(os.environ.get("AUTH_CACHE_TTL_SECONDS", "60"))
# Deliberately much shorter than the success TTL. Long enough to absorb a
# retry storm, short enough that a candidate who just signed in isn't told
# "invalid token" for a minute.
NEGATIVE_TTL_SECONDS = int(os.environ.get("AUTH_CACHE_NEGATIVE_TTL_SECONDS", "5"))
MAX_ENTRIES = int(os.environ.get("AUTH_CACHE_MAX_ENTRIES", "2048"))

_lock = threading.Lock()
_entries: dict[str, tuple[float, Any]] = {}  # token_hash -> (expires_at, user or None)

hits = 0
misses = 0


def _key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_expiry(token: str) -> float | None:
    """The JWT's own `exp`, or None if it can't be read.

    NOT a security check — the signature is never verified here. It is only
    ever used to shorten a cache entry's life, so a malformed or hostile token
    can at worst cause more validation calls, never fewer.
    """
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError, binascii.Error, UnicodeDecodeError):
        return None
    exp = claims.get("exp")
    return float(exp) if isinstance(exp, (int, float)) else None


def get(token: str) -> tuple[bool, Any]:
    """Returns (cached, user). `cached` distinguishes a cached failure (None
    user) from a plain miss."""
    global hits, misses
    if TTL_SECONDS <= 0:
        return False, None
    key = _key(token)
    now = time.time()
    with _lock:
        entry = _entries.get(key)
        if entry and entry[0] > now:
            hits += 1
            return True, entry[1]
        if entry:
            del _entries[key]
        misses += 1
    return False, None


def put(token: str, user: Any) -> None:
    if TTL_SECONDS <= 0:
        return
    now = time.time()
    ttl = TTL_SECONDS if user is not None else NEGATIVE_TTL_SECONDS
    expires_at = now + ttl

    # Never outlive the token itself.
    exp = token_expiry(token)
    if exp is not None:
        expires_at = min(expires_at, exp)
    if expires_at <= now:
        return

    with _lock:
        if len(_entries) >= MAX_ENTRIES:
            # Drop whatever expires soonest. Cheap, and under a real token
            # storm the alternative (unbounded growth) is worse than evicting
            # a still-valid entry — a miss just costs one validation call.
            for stale in sorted(_entries, key=lambda k: _entries[k][0])[: max(1, MAX_ENTRIES // 8)]:
                _entries.pop(stale, None)
        _entries[_key(token)] = (expires_at, user)


def stats() -> dict:
    with _lock:
        total = hits + misses
        return {
            "enabled": TTL_SECONDS > 0,
            "entries": len(_entries),
            "hits": hits,
            "misses": misses,
            "hit_rate": round(hits / total, 3) if total else 0.0,
            "ttl_seconds": TTL_SECONDS,
        }


def clear() -> None:
    global hits, misses
    with _lock:
        _entries.clear()
        hits = misses = 0

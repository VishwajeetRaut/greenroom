"""
Content-addressed response cache for LLM calls.

Why this exists
---------------
The durable, expensive artefacts in this codebase are already cached in
Supabase — generated harnesses and signatures (services.harness_generator) and
generated questions (services.question_generator). What was NOT cached is the
per-interaction LLM traffic that repeats *within and across* sessions:

  * ``test_runner._generate_cases`` fired on EVERY "Run tests" click for a
    problem the interviewer invented on its own. A candidate clicks Run tests
    5-20 times in one coding interview, and every click re-sent the same
    problem statement and re-generated the same six test cases at
    temperature 0.1. That is the single largest repeated-identical-call waste
    in a technical session.
  * ``llm.opening_message`` fired at every session start with only two inputs
    (track, role) — a handful of distinct values across the whole product.

Both are keyed by a small, stable set of inputs, so a content-addressed cache
turns N calls into 1 (or into ``pool_size`` for the opening greeting, which
deliberately keeps some variety — see ``pooled_call``).

Scope and limits
----------------
This is an in-process cache, deliberately: sessions themselves already live in
process memory (services.session_store), so a process-local cache has exactly
the same durability characteristics as the rest of the request path. It does
NOT survive a restart and is not shared between replicas. Entries are bounded
(LRU) and time-limited (TTL) so a long-running process can't grow without
bound.

Never cache anything whose correctness depends on the caller's identity or on
mutable state — everything cached here is a pure function of a prompt.

Accounting
----------
Each entry records how many characters of prompt and response it stands in
for, so a cache hit reports a *measured* saving rather than a guess. See
``stats()`` — those numbers are the input to the model cost matrix.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Iterable

from services.logger import log

# ── env ──────────────────────────────────────────────────────────────────────

CACHE_ENABLED = os.environ.get("LLM_CACHE_ENABLED", "true").lower() == "true"
DEFAULT_TTL_SECONDS = int(os.environ.get("LLM_CACHE_TTL_SECONDS", "86400"))  # 24h
MAX_ENTRIES = int(os.environ.get("LLM_CACHE_MAX_ENTRIES", "512"))

# Rough token estimate for accounting only — never used for billing or for
# sizing a request. 4 chars/token is the usual English-text approximation and
# is close enough for a relative cost comparison between models.
_CHARS_PER_TOKEN = 4


def _est_tokens(chars: int) -> int:
    return chars // _CHARS_PER_TOKEN


# ── key derivation ───────────────────────────────────────────────────────────

def make_key(*parts: Any) -> str:
    """Stable hash of the call's inputs. Uses sort_keys so two dicts that
    differ only in insertion order still collapse onto the same entry."""
    blob = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ── store ────────────────────────────────────────────────────────────────────

class _Entry:
    __slots__ = ("values", "expires_at", "prompt_chars", "hits")

    def __init__(self, expires_at: float, prompt_chars: int):
        # A list even for the single-value case, so ``cached_call`` and
        # ``pooled_call`` share one storage shape — a plain cache is just a
        # pool with size 1.
        self.values: list[tuple[Any, int]] = []  # (value, response_chars)
        self.expires_at = expires_at
        self.prompt_chars = prompt_chars
        self.hits = 0


class _ResponseCache:
    """Thread-safe bounded TTL+LRU store.

    Thread safety is load-bearing, not defensive: LLM calls reach this module
    from FastAPI's ``run_in_threadpool`` and from ``asyncio.to_thread``, so
    concurrent sessions genuinely hit it from different threads.
    """

    def __init__(self, max_entries: int = MAX_ENTRIES):
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._max_entries = max_entries
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self.expirations = 0
        self.saved_prompt_chars = 0
        self.saved_response_chars = 0

    def _get_live(self, key: str) -> _Entry | None:
        """Caller must hold the lock."""
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.expires_at <= time.monotonic():
            del self._entries[key]
            self.expirations += 1
            return None
        self._entries.move_to_end(key)
        return entry

    def take(self, key: str, pool_size: int) -> tuple[bool, Any]:
        """Returns (hit, value). A hit only happens once the entry holds
        ``pool_size`` values — below that the caller is still filling the pool
        and must produce a fresh one."""
        with self._lock:
            entry = self._get_live(key)
            if entry is None or len(entry.values) < pool_size:
                self.misses += 1
                return False, None
            value, response_chars = random.choice(entry.values)
            entry.hits += 1
            self.hits += 1
            self.saved_prompt_chars += entry.prompt_chars
            self.saved_response_chars += response_chars
            return True, value

    def put(self, key: str, value: Any, prompt_chars: int, response_chars: int, ttl: int) -> None:
        with self._lock:
            entry = self._get_live(key)
            if entry is None:
                entry = _Entry(time.monotonic() + ttl, prompt_chars)
                self._entries[key] = entry
            entry.values.append((value, response_chars))
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
                self.evictions += 1

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self.hits = self.misses = self.evictions = self.expirations = 0
            self.saved_prompt_chars = self.saved_response_chars = 0

    def stats(self) -> dict:
        with self._lock:
            total = self.hits + self.misses
            return {
                "enabled": CACHE_ENABLED,
                "entries": len(self._entries),
                "max_entries": self._max_entries,
                "ttl_seconds": DEFAULT_TTL_SECONDS,
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": round(self.hits / total, 3) if total else 0.0,
                "evictions": self.evictions,
                "expirations": self.expirations,
                "est_prompt_tokens_saved": _est_tokens(self.saved_prompt_chars),
                "est_completion_tokens_saved": _est_tokens(self.saved_response_chars),
            }


_cache = _ResponseCache()


# ── public API ───────────────────────────────────────────────────────────────

def _measure(value: Any) -> int:
    if isinstance(value, str):
        return len(value)
    try:
        return len(json.dumps(value, default=str))
    except (TypeError, ValueError):
        return len(str(value))


def pooled_call(
    namespace: str,
    key_parts: Iterable[Any],
    produce: Callable[[], Any],
    *,
    pool_size: int = 1,
    prompt_chars: int = 0,
    ttl: int | None = None,
    cache_falsy: bool = False,
) -> Any:
    """Run ``produce`` unless a cached response for these inputs already exists.

    pool_size > 1 keeps that many distinct responses per key and serves a
    random one once the pool is full. This is for calls that are deliberately
    non-deterministic and user-visible (the opening greeting): collapsing them
    to a single cached string would make every candidate hear the exact same
    sentence forever, so instead the first ``pool_size`` sessions pay for
    generation and every session after that is free while still varying.

    cache_falsy=False means a None/empty result is treated as a transient
    failure and is NOT cached — a failed generation should be retried on the
    next request, not pinned for the whole TTL. (Durable negative results are
    handled where they belong, e.g. harness_generator._UNSUPPORTED_MARKER.)
    """
    if not CACHE_ENABLED:
        return produce()

    key = make_key(namespace, *key_parts)
    hit, value = _cache.take(key, pool_size)
    if hit:
        log.info("llm_cache.hit", namespace=namespace, pool_size=pool_size)
        return value

    value = produce()
    if value or cache_falsy:
        # `ttl is None` rather than `ttl or ...` — ttl=0 is a meaningful value
        # ("expire immediately"), not a request for the default.
        _cache.put(
            key, value, prompt_chars, _measure(value),
            DEFAULT_TTL_SECONDS if ttl is None else ttl,
        )
    log.info(
        "llm_cache.miss",
        namespace=namespace,
        pool_size=pool_size,
        cached=bool(value or cache_falsy),
    )
    return value


def cached_call(
    namespace: str,
    key_parts: Iterable[Any],
    produce: Callable[[], Any],
    *,
    prompt_chars: int = 0,
    ttl: int | None = None,
    cache_falsy: bool = False,
) -> Any:
    """pooled_call with pool_size=1 — for genuinely deterministic calls, where
    serving the same response every time is the desired behaviour."""
    return pooled_call(
        namespace, key_parts, produce,
        pool_size=1, prompt_chars=prompt_chars, ttl=ttl, cache_falsy=cache_falsy,
    )


def stats() -> dict:
    return _cache.stats()


def clear() -> None:
    """Drop everything. Used by tests and by any future admin/debug path."""
    _cache.clear()

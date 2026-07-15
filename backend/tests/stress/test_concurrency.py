"""Concurrency tests for process-wide mutable state.

job_store, session_store and the rate limiter are shared across every request a
replica serves. Their invariants are the ones that break first under load, and
they break silently — a lost job result or a rate limit that stops counting
looks like normal operation from the outside.
"""

from __future__ import annotations

import threading
import time

import pytest
from fastapi import HTTPException

import services.job_store as job_store
import services.session_store as session_store
from services.rate_limit import _buckets, _check_postgres, check_rate_limit

from .conftest import run_concurrent

pytestmark = pytest.mark.stress

CONCURRENCY = 64


# ── job_store ─────────────────────────────────────────────────────────────────

def test_concurrent_job_creation_yields_unique_ids():
    """Every "Run code" click must get its own slot."""
    ids, errors = run_concurrent(lambda i: job_store.create_job(), n=CONCURRENCY)

    assert not errors, f"create_job raised: {errors[:1]}"
    assert len(set(ids)) == CONCURRENCY, "job ids collided"
    assert all(job_store.get_job(j) is not None for j in ids), "a created job was not retrievable"


def test_concurrent_result_writes_are_not_lost():
    """Piston callbacks land on background threads; none may be dropped."""
    ids = [job_store.create_job() for _ in range(CONCURRENCY)]

    def write(i):
        job_store.set_result(ids[i], {"run": {"stdout": str(i), "stderr": "", "code": 0}})
        return ids[i]

    _, errors = run_concurrent(write, n=CONCURRENCY)
    assert not errors, f"set_result raised: {errors[:1]}"

    for i, jid in enumerate(ids):
        job = job_store.get_job(jid)
        assert job["status"] == "done", f"job {jid} left in {job['status']}"
        assert job["result"]["run"]["stdout"] == str(i), "result written to the wrong job"


def test_prune_during_concurrent_creation_is_safe():
    """_prune mutates _jobs while other threads create jobs.

    A prune that iterated without the lock would raise "dictionary changed size
    during iteration" — rare, load-dependent, and fatal to the request.
    """
    def churn(i):
        jid = job_store.create_job()
        job_store.set_result(jid, {"run": {"stdout": "", "stderr": "", "code": 0}})
        return jid

    ids, errors = run_concurrent(churn, n=CONCURRENCY * 4, workers=24)
    assert not errors, f"concurrent create/prune raised: {errors[:1]}"
    assert len(set(ids)) == len(ids)


# ── session_store ─────────────────────────────────────────────────────────────

def test_session_lock_identity_under_concurrent_first_touch():
    """One lock per session, even when requests race to create it.

    session_lock is the mutual-exclusion primitive protecting a session's
    history. If two threads racing the first touch each got their own Lock,
    the session would have no mutual exclusion at all and turns could interleave.
    """
    session_id = "race-session"
    session_store._session_locks.pop(session_id, None)

    locks, errors = run_concurrent(lambda i: session_store.session_lock(session_id), n=CONCURRENCY)

    assert not errors
    assert len({id(lock) for lock in locks}) == 1, "session_lock handed out multiple locks"

    session_store._session_locks.pop(session_id, None)


def test_session_locks_are_distinct_across_sessions():
    """Distinct sessions must never share a lock, or they'd serialise on each other."""
    ids = [f"sess-{i}" for i in range(CONCURRENCY)]
    for sid in ids:
        session_store._session_locks.pop(sid, None)

    locks, errors = run_concurrent(lambda i: session_store.session_lock(ids[i]), n=CONCURRENCY)

    assert not errors
    assert len({id(lock) for lock in locks}) == CONCURRENCY, "sessions shared a lock"

    for sid in ids:
        session_store._session_locks.pop(sid, None)


def test_evict_during_concurrent_lock_acquisition_is_safe():
    """Session expiry races request handling; neither may raise."""
    session_id = "evict-race"

    def churn(i):
        if i % 4 == 0:
            session_store.evict(session_id)
            return "evicted"
        return "locked" if session_store.session_lock(session_id) else "none"

    results, errors = run_concurrent(churn, n=CONCURRENCY * 2)
    assert not errors, f"evict/session_lock raced: {errors[:1]}"
    assert len(results) == CONCURRENCY * 2

    session_store._session_locks.pop(session_id, None)


# ── rate limiter (in-memory path) ─────────────────────────────────────────────

def test_memory_limiter_enforces_limit_exactly_under_burst():
    """The in-memory limiter holds a real lock, so the count must be exact.

    This is the local-dev path. The Postgres path is the deployed one and does
    NOT hold under the same burst — see test_postgres_limiter_race.
    """
    key = "burst-user"
    _buckets.pop(key, None)
    limit = 10
    allowed = []
    barrier = threading.Barrier(CONCURRENCY)

    def attempt(i):
        barrier.wait()  # release all threads into the check together
        try:
            check_rate_limit(key, max_per_minute=limit)
            allowed.append(i)
        except HTTPException as exc:
            assert exc.status_code == 429
        return None

    _, errors = run_concurrent(attempt, n=CONCURRENCY, workers=CONCURRENCY)

    assert not errors, f"limiter raised something other than 429: {errors[:1]}"
    assert len(allowed) == limit, (
        f"expected exactly {limit} requests through, {len(allowed)} got through"
    )

    _buckets.pop(key, None)


# ── rate limiter (Postgres path — the deployed one) ──────────────────────────

class _FakeTable:
    """One Supabase table. Each execute() is individually atomic and pays a
    round-trip cost, which is what a real client does."""

    def __init__(self, store):
        self.store = store
        self.op = None
        self.filters = {}

    def select(self, *a, **kw):
        self.op = "select"
        return self

    def insert(self, row):
        self.op = ("insert", row)
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, k, v):
        self.filters["eq"] = (k, v)
        return self

    def gte(self, k, v):
        return self

    def lt(self, k, v):
        return self

    def execute(self):
        # Network latency. This is the gap between the count and the insert that
        # a real deployment has; without it the race is invisible in-process.
        time.sleep(0.005)
        with self.store.lock:
            if self.op == "select":
                key = self.filters["eq"][1]
                rows = [r for r in self.store.rows if r["user_id"] == key]
                return type("Resp", (), {"count": len(rows), "data": rows})()
            if isinstance(self.op, tuple) and self.op[0] == "insert":
                self.store.rows.append(self.op[1])
                return type("Resp", (), {"count": None, "data": [self.op[1]]})()
            return type("Resp", (), {"count": None, "data": []})()


class _FakeSupabase:
    def __init__(self):
        self.rows = []
        self.lock = threading.Lock()

    def table(self, name):
        return _FakeTable(self)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "BUG: _check_postgres counts rows, then inserts, without atomicity. "
        "Concurrent requests all read a count below the limit before any of "
        "them inserts, so every one is admitted — the limiter provides no "
        "protection under exactly the burst it exists to stop. The limiter is "
        "the cost control in front of the LLM calls. Needs a single atomic "
        "operation (an INSERT ... RETURNING count, or a Postgres function). "
        "Remove this xfail when rate_limit.py is fixed."
    ),
)
def test_postgres_limiter_enforces_limit_under_burst():
    """The deployed limiter must admit at most `limit` requests from a burst."""
    sb = _FakeSupabase()
    limit = 5
    n = 20
    allowed = []
    barrier = threading.Barrier(n)

    def attempt(i):
        barrier.wait()
        try:
            _check_postgres(sb, "burst-user", limit)
            allowed.append(i)
        except HTTPException as exc:
            assert exc.status_code == 429
        return None

    _, errors = run_concurrent(attempt, n=n, workers=n)

    assert not errors, f"limiter raised unexpectedly: {errors[:1]}"
    assert len(allowed) <= limit, (
        f"rate limit of {limit} admitted {len(allowed)} concurrent requests"
    )

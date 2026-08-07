"""JWT validation cache.

This is the most security-adjacent code added, so the tests are weighted
toward the ways caching auth can go wrong rather than toward it being fast:
raw tokens must never be stored, an entry must never outlive the token's own
expiry, and disabling it must genuinely disable it.
"""
import base64
import json
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

import auth
from services import auth_cache


@pytest.fixture(autouse=True)
def _clean():
    auth_cache.clear()
    yield
    auth_cache.clear()


def _jwt(exp: float | None = None) -> str:
    """A structurally valid JWT. The signature is never checked — Supabase is
    the authority; the claim is only read to expire the cache sooner."""
    claims = {"sub": "user-1"}
    if exp is not None:
        claims["exp"] = exp
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


# ── never store the credential ───────────────────────────────────────────────

def test_the_raw_token_is_never_kept_in_memory():
    """Otherwise this dict is a bag of live credentials, readable from any
    traceback or heap dump."""
    token = _jwt()
    auth_cache.put(token, "user")
    assert token not in auth_cache._entries
    assert all(token not in key for key in auth_cache._entries)


# ── expiry ───────────────────────────────────────────────────────────────────

def test_a_hit_returns_the_cached_user():
    token = _jwt()
    auth_cache.put(token, "user-object")
    cached, user = auth_cache.get(token)
    assert cached and user == "user-object"


def test_a_miss_is_distinguishable_from_a_cached_failure():
    """A cached 401 must not look like 'never seen this token'."""
    unseen_cached, _ = auth_cache.get(_jwt())
    assert not unseen_cached

    bad = _jwt()
    auth_cache.put(bad, None)
    cached, user = auth_cache.get(bad)
    assert cached is True and user is None


def test_entry_never_outlives_the_token_itself():
    """A token expiring in 2s must not stay cached for the full 60s TTL."""
    token = _jwt(exp=time.time() + 2)
    auth_cache.put(token, "user")
    expires_at, _ = auth_cache._entries[auth_cache._key(token)]
    assert expires_at <= time.time() + 2.01


def test_an_already_expired_token_is_not_cached_at_all():
    token = _jwt(exp=time.time() - 1)
    auth_cache.put(token, "user")
    assert auth_cache.get(token)[0] is False


def test_a_token_with_no_exp_claim_uses_the_default_ttl():
    token = _jwt()
    auth_cache.put(token, "user")
    expires_at, _ = auth_cache._entries[auth_cache._key(token)]
    assert expires_at > time.time() + auth_cache.TTL_SECONDS - 2


@pytest.mark.parametrize("token", ["", "not-a-jwt", "a.b", "a.!!!.c", "a." + "x" * 10 + ".c"])
def test_malformed_tokens_do_not_raise(token):
    """A hostile token can at worst cause MORE validation calls, never fewer."""
    assert auth_cache.token_expiry(token) is None
    auth_cache.put(token, "user")
    auth_cache.get(token)


def test_expired_entries_are_evicted_on_read():
    token = _jwt()
    with patch.object(auth_cache, "TTL_SECONDS", 1):
        auth_cache.put(token, "user")
        auth_cache._entries[auth_cache._key(token)] = (time.time() - 1, "user")
        assert auth_cache.get(token)[0] is False
    assert auth_cache._key(token) not in auth_cache._entries


# ── bounds ───────────────────────────────────────────────────────────────────

def test_the_cache_is_bounded():
    """A token storm must not grow this without limit."""
    with patch.object(auth_cache, "MAX_ENTRIES", 64):
        for i in range(300):
            auth_cache.put(f"header.{i}.sig", f"user-{i}")
    assert len(auth_cache._entries) <= 64


def test_disabling_the_cache_actually_disables_it():
    token = _jwt()
    with patch.object(auth_cache, "TTL_SECONDS", 0):
        auth_cache.put(token, "user")
        assert auth_cache.get(token) == (False, None)


# ── integration with get_current_user ────────────────────────────────────────

def _supabase_returning(user):
    client = MagicMock()
    client.auth.get_user.return_value = MagicMock(user=user)
    return client


def test_a_validated_token_is_only_looked_up_once():
    """The whole point: one network round-trip per token per TTL, not one per
    request."""
    token = _jwt()
    client = _supabase_returning(MagicMock(id="u1", email="a@b.c"))
    with patch("auth.get_supabase", return_value=client):
        for _ in range(20):
            user = auth.get_current_user(f"Bearer {token}")
    assert client.auth.get_user.call_count == 1
    assert user.id == "u1"


def test_a_retry_storm_with_one_bad_token_costs_one_lookup():
    """The negative cache exists for exactly this — it is the scenario that
    used to saturate the threadpool."""
    token = _jwt()
    client = MagicMock()
    client.auth.get_user.side_effect = OSError("supabase unreachable")
    with patch("auth.get_supabase", return_value=client), \
         patch.object(auth, "RETRY_PAUSE_SECONDS", 0):
        for _ in range(20):
            with pytest.raises(HTTPException) as exc:
                auth.get_current_user(f"Bearer {token}")
            assert exc.value.status_code == 401
    # 2 calls = the single request that actually ran, with its one retry.
    assert client.auth.get_user.call_count == 2


def test_the_retry_pause_cannot_starve_the_threadpool():
    """It runs in FastAPI's 40-worker threadpool, shared with every LLM call
    and DB write in the app. The original 0.3s meant ~133 failed auths/sec
    could stall every in-flight interview."""
    assert auth.RETRY_PAUSE_SECONDS <= 0.1


def test_a_missing_bearer_header_is_rejected_without_touching_supabase():
    with patch("auth.get_supabase") as get_supabase:
        for header in (None, "", "Basic abc", "Bearer "):
            with pytest.raises(HTTPException):
                auth.get_current_user(header)
    get_supabase.assert_not_called()


def test_two_different_tokens_do_not_share_an_entry():
    a, b = _jwt(), "header.other.sig"
    auth_cache.put(a, "user-a")
    auth_cache.put(b, "user-b")
    assert auth_cache.get(a)[1] == "user-a"
    assert auth_cache.get(b)[1] == "user-b"


def test_stats_report_the_hit_rate():
    token = _jwt()
    auth_cache.get(token)          # miss
    auth_cache.put(token, "user")
    auth_cache.get(token)          # hit
    stats = auth_cache.stats()
    assert stats["hits"] == 1 and stats["misses"] == 1
    assert stats["hit_rate"] == 0.5

import os
import time
from dataclasses import dataclass

from fastapi import Header, HTTPException, status

from services import auth_cache
from services.supabase_client import get_supabase

# Pause between the two validation attempts. Small on purpose — see the
# comment in get_current_user about threadpool starvation.
RETRY_PAUSE_SECONDS = float(os.environ.get("AUTH_RETRY_PAUSE_SECONDS", "0.05"))


@dataclass(frozen=True)
class AuthenticatedUser:
    id: str
    email: str | None = None


def get_current_user(authorization: str | None = Header(default=None)) -> AuthenticatedUser:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bearer token")

    # Validated tokens are cached briefly (services.auth_cache). Without it
    # every single request paid for a network round-trip to Supabase before
    # doing any work — measured at ~312ms per authenticated request under
    # load. See that module for the staleness trade and why it's bounded.
    cached, cached_user = auth_cache.get(token)
    if cached:
        if cached_user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return cached_user

    supabase = get_supabase()
    if not supabase:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database is not configured")

    # A transient network error talking to Supabase looks identical to an
    # actually-invalid token if we don't retry — which would otherwise log a
    # candidate out (or fail an unrelated request, e.g. bulk delete) for a
    # blip that had nothing to do with their token.
    #
    # The pause between attempts is deliberately tiny. This function runs in
    # FastAPI's threadpool, which defaults to 40 workers and is SHARED with
    # every run_in_threadpool call in the app (LLM calls, persistence,
    # question selection). The original 0.3s blocking sleep meant ~133 failed
    # auths per second could saturate that pool and stall every in-flight
    # interview — and a Supabase blip is exactly when those retries all fire
    # at once. Combined with the negative cache above, a retry storm now costs
    # one round-trip per token rather than one per request.
    user = None
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            response = supabase.auth.get_user(token)
            user = response.user
            last_exc = None
            break
        except Exception as exc:
            last_exc = exc
            if attempt == 0:
                time.sleep(RETRY_PAUSE_SECONDS)
    if last_exc is not None:
        auth_cache.put(token, None)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from last_exc

    if not user:
        auth_cache.put(token, None)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    authenticated = AuthenticatedUser(id=str(user.id), email=user.email)
    auth_cache.put(token, authenticated)
    return authenticated

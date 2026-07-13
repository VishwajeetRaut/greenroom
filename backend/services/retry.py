"""
Retry-with-exponential-backoff helper for external calls (Piston, LLM).

Usage:
    from services.retry import with_retry

    result = await with_retry(
        lambda: piston_client.post(...),
        attempts=3,
        base_delay=1.0,
        label="piston.run",
    )
"""

from __future__ import annotations

import asyncio
from typing import Callable, TypeVar

from services.logger import log

T = TypeVar("T")


async def with_retry(
    fn: Callable,
    attempts: int = 3,
    base_delay: float = 1.0,
    label: str = "retry",
) -> T:
    """
    Call `fn()` up to `attempts` times with exponential backoff.
    fn may be a coroutine function or a regular callable.
    Raises the last exception if all attempts fail.
    """
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            result = fn()
            if asyncio.iscoroutine(result):
                return await result
            return result
        except Exception as exc:
            last_exc = exc
            if attempt < attempts:
                delay = base_delay * (2 ** (attempt - 1))
                log.warning(
                    f"{label}.retry",
                    attempt=attempt,
                    max_attempts=attempts,
                    delay_s=delay,
                    error=str(exc),
                )
                await asyncio.sleep(delay)
            else:
                log.error(f"{label}.failed", attempts=attempts, error=str(exc))
    assert last_exc is not None  # guaranteed: attempts >= 1, loop only exits via return or this raise
    raise last_exc

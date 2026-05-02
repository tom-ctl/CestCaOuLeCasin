"""Retry helpers for transient API failures."""

from __future__ import annotations

import asyncio
import functools
import logging
from collections.abc import Awaitable, Callable
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
T = TypeVar("T")


def async_retry(
    attempts: int = 3,
    delay_seconds: float = 1.0,
    backoff: float = 2.0,
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    """Retry an async function with exponential backoff."""

    def decorator(func: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        logger = logging.getLogger(func.__module__)

        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            wait = delay_seconds
            last_error: Exception | None = None
            for attempt in range(1, attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001 - exchange libraries raise broad errors.
                    last_error = exc
                    if attempt == attempts:
                        break
                    logger.warning(
                        "Retrying %s after error on attempt %s/%s: %s",
                        func.__name__,
                        attempt,
                        attempts,
                        exc,
                    )
                    await asyncio.sleep(wait)
                    wait *= backoff
            assert last_error is not None
            raise last_error

        return wrapper

    return decorator

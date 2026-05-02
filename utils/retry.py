"""Retry helpers for transient API failures."""

from __future__ import annotations

import asyncio
import functools
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
        logger = get_logger("retry")

        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            wait = delay_seconds
            last_error: Exception | None = None
            for attempt in range(1, attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001 - exchange libraries raise broad errors.
                    last_error = exc
                    if "does not have market symbol" in str(exc) or "Invalid exchange symbol" in str(exc):
                        logger.warning("Non-retryable symbol error in %s: %s", func.__name__, exc)
                        break
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
            logger.error("Retry exhausted for %s after %s attempts: %s", func.__name__, attempts, last_error)
            raise last_error

        return wrapper

    return decorator
from utils.logger import get_logger

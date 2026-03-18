"""Retry decorator using tenacity with structured logging."""

from __future__ import annotations

from typing import Any

from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.utils.logger import get_logger

log = get_logger("retry")


def _log_retry(state: RetryCallState) -> None:
    """Log each retry attempt."""
    exc = state.outcome.exception() if state.outcome else None
    log.warning(
        "retrying",
        attempt=state.attempt_number,
        error=str(exc) if exc else None,
        fn=state.fn.__name__ if state.fn else "unknown",
    )


def with_retry(
    max_attempts: int = 3,
    min_wait: float = 1.0,
    max_wait: float = 30.0,
    retry_on: tuple[type[Exception], ...] = (Exception,),
    **kwargs: Any,
) -> Any:
    """Configurable retry decorator with exponential backoff."""
    return retry(
        retry=retry_if_exception_type(retry_on),
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(min=min_wait, max=max_wait),
        before_sleep=_log_retry,
        reraise=True,
        **kwargs,
    )

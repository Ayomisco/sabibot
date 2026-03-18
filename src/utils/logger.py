"""Structured logging with structlog + rich console output."""

from __future__ import annotations

import logging
import sys

import structlog
from rich.console import Console
from rich.logging import RichHandler

from src.config import settings

_configured = False
console = Console()


def setup_logging() -> None:
    """Configure structured logging once. Idempotent."""
    global _configured
    if _configured:
        return
    _configured = True

    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    # Rich handler for pretty console output
    logging.basicConfig(
        level=log_level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(
            console=console, rich_tracebacks=True, show_path=False)],
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer() if sys.stderr.isatty(
            ) else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Get a named logger. Call setup_logging() first."""
    setup_logging()
    return structlog.get_logger(name)

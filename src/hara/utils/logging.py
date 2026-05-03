"""Structured logging setup for HARA.

Use `configure_logging()` once at process startup (CLI entrypoint or API factory).
After that, `structlog.get_logger(__name__)` returns a logger with the configured
processors. Log levels: DEBUG | INFO | WARNING | ERROR.
"""

from __future__ import annotations

import logging
import sys
from typing import Literal

import structlog
from structlog.types import Processor

LogFormat = Literal["pretty", "json"]


def configure_logging(level: str = "INFO", format: LogFormat = "pretty") -> None:
    """Configure structlog + stdlib logging integration.

    Args:
        level: One of DEBUG, INFO, WARNING, ERROR.
        format: 'pretty' (Rich-style for dev) or 'json' (one-line for prod).

    Raises:
        ValueError: If format is not 'pretty' or 'json'.
    """
    if format not in ("pretty", "json"):
        raise ValueError(f"format must be 'pretty' or 'json', got {format!r}")

    log_level = getattr(logging, level.upper(), logging.INFO)

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if format == "json":
        renderer: Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,  # tests reconfigure between runs
    )

    # Pipe stdlib logging through structlog for libs that use logging.
    # We avoid `logging.basicConfig(force=True)` because it would strip
    # pytest's caplog handler (and any other test/runtime-installed handlers).
    # Instead, replace any prior handler we previously installed and ensure a
    # stream handler routes records to stdout — this lets both caplog (which
    # snoops records) and stream consumers see structured output.
    root = logging.getLogger()
    for existing in list(root.handlers):
        if getattr(existing, "_hara_logging", False):
            root.removeHandler(existing)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler._hara_logging = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(log_level)

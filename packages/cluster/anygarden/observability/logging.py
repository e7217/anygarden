"""Structlog configuration for the Anygarden server.

Production renders JSON (one object per line, ready for a log
collector); development keeps the human-friendly console renderer.
``request_id`` (and ``trace_id``) bound via
``structlog.contextvars.bind_contextvars`` in the WebSocket handler are
merged into every record, so logs join the OTEL traces emitted by
``observability.tracing`` (#420).
"""

from __future__ import annotations

import logging
from typing import Any

import structlog


def _add_otel_context(
    _logger: Any, _method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Best-effort: stamp the active OTEL span's ids onto the record.

    In the cluster-reconstruction model spans are rarely "current" in
    the coroutine that logs, so this usually adds nothing — the durable
    correlation key is the ``request_id`` bound via contextvars. It is
    kept for the code paths that *do* run inside an active span and must
    never raise.
    """
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        ctx = span.get_span_context() if span is not None else None
        if ctx is not None and ctx.is_valid:
            event_dict.setdefault("trace_id", format(ctx.trace_id, "032x"))
            event_dict.setdefault("span_id", format(ctx.span_id, "016x"))
    except Exception:  # noqa: BLE001 — logging must never crash on this
        pass
    return event_dict


def configure_logging(log_level: str = "INFO", *, dev: bool = False) -> None:
    """Set up structlog — console in dev, JSON in production."""
    renderer: Any = (
        structlog.dev.ConsoleRenderer()
        if dev
        else structlog.processors.JSONRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _add_otel_context,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

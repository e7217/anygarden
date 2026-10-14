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


class BestEffortPrintLogger(structlog.PrintLogger):
    """``PrintLogger`` whose write failures never reach the caller.

    ``PrintLogger`` writes with ``print(..., flush=True)``; on a full disk
    that raises ``OSError(ENOSPC)`` from *whatever* code logged. On the node
    shutdown path (spawner ``drain_complete``, turn recovery, ...) such a
    raise made a confirmed process cleanup look unconfirmed, and the node
    then refused every restart until manual recovery (NODE-10). Losing a log
    line is always preferable, so every structlog write is best effort.
    Real failures (ownership/state files) still raise from their own I/O.
    """

    def msg(self, message: str) -> None:
        try:
            super().msg(message)
        except (OSError, ValueError):  # ValueError: write to a closed stream
            pass

    log = debug = info = warn = warning = msg
    fatal = failure = err = error = critical = exception = msg


class BestEffortPrintLoggerFactory:
    """``PrintLoggerFactory`` producing :class:`BestEffortPrintLogger`."""

    def __init__(self, file: Any = None) -> None:
        self._file = file

    def __call__(self, *args: Any) -> BestEffortPrintLogger:
        return BestEffortPrintLogger(self._file)


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
        logger_factory=BestEffortPrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

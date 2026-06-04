"""Logging correlation + renderer selection (#420).

``configure_logging`` renders JSON in production and console in dev, and
``request_id`` bound via ``structlog.contextvars`` surfaces on every
record (the durable correlation key with the OTEL traces).
"""

from __future__ import annotations

import json

import structlog

from anygarden.observability.logging import configure_logging


def test_production_renders_json_with_bound_request_id(capsys):
    configure_logging("INFO", dev=False)
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id="req-abc")
    try:
        structlog.get_logger("t").info("hello", room_id="room-1")
    finally:
        structlog.contextvars.clear_contextvars()

    line = capsys.readouterr().out.strip().splitlines()[-1]
    record = json.loads(line)  # production output must be valid JSON
    assert record["event"] == "hello"
    assert record["request_id"] == "req-abc"
    assert record["room_id"] == "room-1"
    assert record["level"] == "info"


def test_dev_uses_console_renderer_not_json(capsys):
    configure_logging("INFO", dev=True)
    structlog.contextvars.clear_contextvars()
    structlog.get_logger("t").info("hi")
    out = capsys.readouterr().out.strip()
    # Console renderer is not valid JSON.
    try:
        json.loads(out.splitlines()[-1])
        is_json = True
    except (ValueError, IndexError):
        is_json = False
    assert is_json is False
    assert "hi" in out


def test_otel_context_processor_is_safe_without_active_span(capsys):
    # No active span → processor adds nothing and never raises.
    configure_logging("INFO", dev=False)
    structlog.contextvars.clear_contextvars()
    structlog.get_logger("t").info("no-span")
    record = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert "trace_id" not in record

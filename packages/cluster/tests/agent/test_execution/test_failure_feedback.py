"""Only safe, unambiguous CLI failures become actionable codes."""

import json

import pytest

from anygarden.agent.runtime.execution.failure_feedback import (
    CodexFailureTracker,
    classify_failure,
    redact_secrets,
)


@pytest.mark.parametrize(
    ("engine", "message", "expected"),
    [
        ("codex-cli", "Not logged in. Run codex login", "ENGINE_AUTH_ERROR"),
        ("codex-cli", "unexpected status 401 Unauthorized: secret-key", "ENGINE_AUTH_ERROR"),
        ("pi-cli", "Authentication failed: secret-key", "ENGINE_AUTH_ERROR"),
        ("pi-cli", "Unknown provider: my-local", "PI_PROVIDER_ERROR"),
        ("pi-cli", "Provider my-local not found", "PI_PROVIDER_ERROR"),
        ("pi-cli", "401: secret-key", "ENGINE_AUTH_ERROR"),
        ("codex-cli", "403 Forbidden: model access denied", None),
        ("pi-cli", "unknown provider; authentication failed", None),
        ("pi-cli", "model not found", None),
        ("codex-cli", "DO-NOT-PUBLISH", None),
        ("codex-cli", "authentication failed" * 500, None),
        ("pi-cli", None, None),
        ("pi-cli", {"message": "authentication failed"}, None),
    ],
)
def test_classify_failure(engine, message, expected):
    assert classify_failure(engine, message) == expected


# -- codex-cli turn failure diagnosis (live benchmark 2026-10-12) ----------



def _api_error(status, etype, message):
    return json.dumps(
        {"type": "error", "status": status, "error": {"type": etype, "message": message}}
    )


def _track(events):
    tracker = CodexFailureTracker()
    for event in events:
        tracker.observe(event)
    return tracker


def _failed(message, *, before=()):
    return [
        {"type": "thread.started", "thread_id": "t"},
        {"type": "turn.started"},
        *before,
        {"type": "error", "message": message},
        {"type": "turn.failed", "error": {"message": message}},
    ]


_COMMAND = {"type": "item.started", "item": {"type": "command_execution", "command": "ls"}}


@pytest.mark.req("EXE-10")
def test_tracker_429_before_any_item_is_transient():
    tracker = _track(_failed(_api_error(429, "rate_limit_exceeded", "Rate limit reached")))
    assert tracker.status == 429
    assert tracker.transient is True
    assert tracker.code() == "MODEL_TRANSIENT_FAILURE"
    assert "429" in tracker.reason and "Rate limit reached" in tracker.reason


@pytest.mark.req("EXE-10")
def test_tracker_500_after_side_effect_is_not_transient():
    tracker = _track(_failed(_api_error(500, "server_error", "boom"), before=[_COMMAND]))
    assert tracker.side_effect_started is True
    assert tracker.transient is False
    assert tracker.code() is None


@pytest.mark.req("EXE-10")
def test_tracker_400_invalid_request_is_not_transient_with_reason():
    message = _api_error(400, "invalid_request_error", "The 'gpt-x' model is not supported, see 500 docs")
    tracker = _track(_failed(message))
    assert tracker.status == 400
    assert tracker.transient is False
    assert tracker.code() != "MODEL_TRANSIENT_FAILURE"
    assert tracker.reason.startswith("status 400")
    assert "invalid_request_error" in tracker.reason


@pytest.mark.parametrize(
    "item_type", ["agent_message", "reasoning", "error", "todo_list"]
)
def test_tracker_non_side_effect_items(item_type):
    events = _failed(
        "stream disconnected before completion: error sending request",
        before=[{"type": "item.completed", "item": {"type": item_type, "text": "x"}}],
    )
    tracker = _track(events)
    assert tracker.side_effect_started is False
    assert tracker.transient is True
    assert tracker.transient is True


@pytest.mark.parametrize("item_type", ["file_change", "mcp_tool_call", None])
def test_tracker_side_effect_items(item_type):
    item = {"type": "item.started", "item": {"type": item_type} if item_type else {}}
    tracker = _track(_failed(_api_error(503, "overloaded", "busy"), before=[item]))
    assert tracker.side_effect_started is True
    assert tracker.transient is False


def test_tracker_top_level_error_without_turn_failed():
    tracker = _track([
        {"type": "turn.started"},
        {"type": "error", "message": _api_error(503, "server_error", "unavailable")},
    ])
    assert tracker.status == 503 and tracker.transient is True


def test_tracker_turn_failed_wins_over_retry_notices():
    tracker = _track([
        {"type": "turn.started"},
        {"type": "error", "message": "Reconnecting... 1/5 (stream disconnected)"},
        {"type": "turn.failed", "error": {"message": _api_error(401, "invalid_api_key", "bad key")}},
    ])
    assert tracker.status == 401 and tracker.transient is False


def test_tracker_without_failure_events_is_empty_and_not_transient():
    tracker = _track([{"type": "turn.started"}, _COMMAND])
    assert tracker.reason is None and tracker.transient is False and tracker.code() is None


def test_tracker_two_turn_failures_are_ambiguous():
    msg = _api_error(429, "rate_limit_exceeded", "slow down")
    tracker = _track([
        {"type": "turn.failed", "error": {"message": msg}},
        {"type": "turn.failed", "error": {"message": msg}},
    ])
    assert tracker.transient is False and tracker.code() is None


def test_tracker_reason_is_truncated_and_redacted(monkeypatch):
    monkeypatch.setenv("ANYGARDEN_AGENT_TOKEN", "agent-token-value-1234")
    secret = "sk-proj-" + "a" * 40
    message = _api_error(
        500, "server_error",
        f"key {secret} token agent-token-value-1234 Bearer abc.def.ghi " + "x" * 800,
    )
    tracker = _track(_failed(message))
    assert len(tracker.reason) <= 300
    assert secret not in tracker.reason
    assert "agent-token-value-1234" not in tracker.reason
    assert "abc.def.ghi" not in tracker.reason
    assert "[REDACTED]" in tracker.reason


def test_redact_secrets_patterns():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJlLXZhbHVl"
    text = redact_secrets(f"api_key=hunter2hunter2 {jwt} Authorization: Bearer tok123")
    assert "hunter2hunter2" not in text and jwt not in text and "tok123" not in text


@pytest.mark.parametrize(
    "message",
    [
        "stream disconnected before completion: Transport error",
        "request timed out",
        _api_error(500, "server_error", "internal"),
    ],
)
def test_classify_codex_transient_messages(message):
    assert classify_failure("codex-cli", message) == "MODEL_TRANSIENT_FAILURE"


def test_classify_codex_structured_4xx_is_not_transient_even_with_signal_words():
    message = _api_error(400, "invalid_request_error", "context too large; retry after timeout 500")
    assert classify_failure("codex-cli", message) != "MODEL_TRANSIENT_FAILURE"


_CAPACITY = "Selected model is at capacity. Please try a different model."


@pytest.mark.req("EXE-10")
@pytest.mark.parametrize("message", [_CAPACITY, "The model is at capacity right now", "provider at capacity"])
def test_classify_codex_model_capacity_is_transient(message):
    # Live benchmark 2026-10-13: codex ENGINE_ERROR with this text.
    assert classify_failure("codex-cli", message) == "MODEL_TRANSIENT_FAILURE"


@pytest.mark.req("EXE-10")
def test_tracker_model_capacity_is_transient_only_without_side_effects():
    tracker = _track(_failed(_CAPACITY))
    assert tracker.side_effect_started is False
    assert tracker.transient is True
    assert tracker.transient is True
    assert tracker.code() == "MODEL_TRANSIENT_FAILURE"
    # After a side effect started the failure is never retried automatically.
    after_tool = _track(_failed(_CAPACITY, before=[{"type": "item.started",
                                                    "item": {"type": "mcp_tool_call"}}]))
    assert after_tool.side_effect_started is True
    assert after_tool.transient is False
    assert after_tool.code() != "MODEL_TRANSIENT_FAILURE"


@pytest.mark.req("EXE-10")
@pytest.mark.parametrize(
    "unknown",
    [
        {"type": "item.started", "item": {"type": "browser_action", "url": "x"}},
        {"type": "item.completed", "item": {"id": "i1"}},  # missing item type
        {"type": "item.updated", "item": "not-a-dict"},
        {"type": "tool.invoked", "name": "deploy"},  # unknown top-level event
    ],
)
def test_tracker_unknown_item_or_event_type_counts_as_side_effect(unknown):
    """Fail closed: anything not known side-effect free blocks a transient
    retry, so a newer codex event type can never re-run real work."""
    tracker = _track(_failed(_api_error(429, "rate_limit_exceeded", "Rate limit"),
                             before=[unknown]))
    assert tracker.side_effect_started is True
    assert tracker.transient is False


@pytest.mark.req("EXE-10")
def test_tracker_known_side_effect_free_items_stay_transient():
    from anygarden.agent.runtime.execution.failure_feedback import SIDE_EFFECT_FREE_ITEMS

    before = [
        {"type": phase, "item": {"type": item_type}}
        for item_type in sorted(SIDE_EFFECT_FREE_ITEMS)
        for phase in ("item.started", "item.updated", "item.completed")
    ]
    tracker = _track(_failed(_api_error(429, "rate_limit_exceeded", "Rate limit"),
                             before=before)
                     + [{"type": "turn.completed", "usage": {}}])
    assert tracker.side_effect_started is False
    assert tracker.transient is True

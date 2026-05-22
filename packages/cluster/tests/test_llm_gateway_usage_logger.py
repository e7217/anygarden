"""Tests for :mod:`anygarden.llm_gateway.usage_logger` (#197).

Two pure functions over dict payloads — no DB, no HTTP. Each shape
(Anthropic + OpenAI, JSON + SSE) gets a positive case; missing fields
degrade to ``None`` rather than raising so the caller can still
record "which model was called" even when token counts are unavailable.
"""

from __future__ import annotations

from anygarden.llm_gateway.usage_logger import (
    ParsedUsage,
    parse_json_usage,
    parse_stream_event,
)


class TestParseJsonUsage:
    def test_anthropic_shape(self) -> None:
        body = {
            "id": "msg_abc",
            "content": [{"type": "text", "text": "hi"}],
            "usage": {"input_tokens": 123, "output_tokens": 45},
        }
        result = parse_json_usage(body)
        assert result == ParsedUsage(prompt_tokens=123, completion_tokens=45)

    def test_openai_shape(self) -> None:
        body = {
            "id": "chatcmpl-xyz",
            "choices": [],
            "usage": {"prompt_tokens": 77, "completion_tokens": 88},
        }
        result = parse_json_usage(body)
        assert result == ParsedUsage(prompt_tokens=77, completion_tokens=88)

    def test_missing_usage_returns_empty(self) -> None:
        # No ``usage`` key at all — still returns a valid object so
        # the caller can record the request without token counts.
        result = parse_json_usage({"id": "x"})
        assert result == ParsedUsage(prompt_tokens=None, completion_tokens=None)

    def test_partial_usage_fields(self) -> None:
        # Only prompt_tokens present — completion stays None.
        body = {"usage": {"prompt_tokens": 10}}
        result = parse_json_usage(body)
        assert result.prompt_tokens == 10
        assert result.completion_tokens is None


class TestParseStreamEvent:
    def test_non_usage_event_returns_none(self) -> None:
        # ``content_block_delta`` — has no usage, should be ignored.
        event = {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "hello"},
        }
        assert parse_stream_event(event) is None

    def test_anthropic_message_delta_with_usage(self) -> None:
        # Anthropic streams the final token counts in a
        # ``message_delta`` event just before ``message_stop``.
        event = {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 42},
        }
        result = parse_stream_event(event)
        assert result is not None
        assert result.completion_tokens == 42

    def test_anthropic_message_start_with_input_tokens(self) -> None:
        # ``message_start`` includes the prompt token count up front.
        event = {
            "type": "message_start",
            "message": {
                "id": "msg_1",
                "usage": {"input_tokens": 200, "output_tokens": 0},
            },
        }
        result = parse_stream_event(event)
        assert result is not None
        assert result.prompt_tokens == 200

    def test_openai_final_chunk_with_usage(self) -> None:
        # OpenAI streams usage on the last chunk when
        # ``stream_options.include_usage=True`` was requested.
        event = {
            "id": "chatcmpl-final",
            "object": "chat.completion.chunk",
            "choices": [],
            "usage": {"prompt_tokens": 15, "completion_tokens": 30},
        }
        result = parse_stream_event(event)
        assert result == ParsedUsage(prompt_tokens=15, completion_tokens=30)

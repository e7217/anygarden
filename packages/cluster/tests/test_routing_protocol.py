"""Pure-function tests for the routing prompt + parser (#313)."""

from __future__ import annotations

import pytest

from doorae.routing.protocol import (
    ROUTING_REQUEST_MARKER,
    ROUTING_RESPONSE_MARKER,
    _AgentLine,
    _TaskLine,
    format_routing_prompt,
    parse_routing_response,
    try_parse_routing_response,
)


class TestFormatRoutingPrompt:
    def test_includes_marker_with_request_id(self) -> None:
        body = format_routing_prompt(
            request_id="abc",
            room_name="travel-bookings",
            agents=[_AgentLine("a1", "emma", "Backend.")],
            tasks=[_TaskLine("t1", "Add login")],
        )
        assert ROUTING_REQUEST_MARKER + " id=abc]" in body
        assert ROUTING_RESPONSE_MARKER + " id=abc]" in body
        assert "travel-bookings" in body
        assert "emma" in body
        assert "Backend." in body
        assert "t1" in body

    def test_omits_description_when_blank(self) -> None:
        body = format_routing_prompt(
            request_id="abc",
            room_name="r",
            agents=[_AgentLine("a1", "emma", None)],
            tasks=[_TaskLine("t1", "x")],
        )
        # No trailing colon when description is missing — we want
        # ``- emma (a1)`` not ``- emma (a1): ``.
        assert "- emma (a1)\n" in body or "- emma (a1)" in body
        assert "(a1):" not in body

    def test_collapses_multiline_description(self) -> None:
        body = format_routing_prompt(
            request_id="abc",
            room_name="r",
            agents=[_AgentLine("a1", "emma", "Line one.\nLine two.")],
            tasks=[_TaskLine("t1", "x")],
        )
        # newlines inside descriptions would break the agent-list
        # markdown structure; the helper collapses them to spaces.
        assert "Line one. Line two." in body


class TestParseRoutingResponse:
    def test_pure_json_response(self) -> None:
        content = (
            f"{ROUTING_RESPONSE_MARKER} id=abc]\n"
            '{"t1": "a1", "t2": "a2"}'
        )
        result = parse_routing_response("abc", content)
        assert result.ok
        assert result.mapping == {"t1": "a1", "t2": "a2"}

    def test_code_fenced_json(self) -> None:
        # LLMs often wrap structured output in markdown fences.
        content = (
            f"{ROUTING_RESPONSE_MARKER} id=abc]\n"
            "```json\n"
            '{"t1": "a1"}\n'
            "```"
        )
        result = parse_routing_response("abc", content)
        assert result.ok
        assert result.mapping == {"t1": "a1"}

    def test_leading_prose_before_marker_is_ignored(self) -> None:
        content = (
            "Here is the routing decision:\n"
            "\n"
            f"{ROUTING_RESPONSE_MARKER} id=abc]\n"
            '{"t1": "a1"}'
        )
        result = parse_routing_response("abc", content)
        assert result.ok
        assert result.mapping == {"t1": "a1"}

    def test_id_mismatch_rejected(self) -> None:
        content = (
            f"{ROUTING_RESPONSE_MARKER} id=stale]\n"
            '{"t1": "a1"}'
        )
        result = parse_routing_response("abc", content)
        assert not result.ok
        assert "does not match" in (result.error or "")

    def test_marker_missing(self) -> None:
        result = parse_routing_response("abc", '{"t1": "a1"}')
        assert not result.ok
        assert "marker not found" in (result.error or "")

    def test_invalid_json(self) -> None:
        content = (
            f"{ROUTING_RESPONSE_MARKER} id=abc]\n"
            "not actually json"
        )
        result = parse_routing_response("abc", content)
        assert not result.ok
        assert "invalid JSON" in (result.error or "")

    def test_array_payload_rejected(self) -> None:
        content = (
            f"{ROUTING_RESPONSE_MARKER} id=abc]\n"
            '["a1", "a2"]'
        )
        result = parse_routing_response("abc", content)
        assert not result.ok
        assert "JSON object" in (result.error or "")

    def test_non_string_value_rejected(self) -> None:
        content = (
            f"{ROUTING_RESPONSE_MARKER} id=abc]\n"
            '{"t1": 42}'
        )
        result = parse_routing_response("abc", content)
        assert not result.ok
        assert "non-string" in (result.error or "")


class TestTryParseRoutingResponse:
    def test_returns_none_when_marker_absent(self) -> None:
        # Regular chat messages must not match; otherwise the WS
        # hook would resolve random Futures.
        assert try_parse_routing_response("hello world") is None

    def test_extracts_request_id_and_result(self) -> None:
        content = (
            f"{ROUTING_RESPONSE_MARKER} id=xyz]\n"
            '{"t1": "a1"}'
        )
        out = try_parse_routing_response(content)
        assert out is not None
        rid, result = out
        assert rid == "xyz"
        assert result.ok
        assert result.mapping == {"t1": "a1"}

    def test_extracts_id_even_when_payload_is_garbage(self) -> None:
        # The hook should still resolve the Future (with an error
        # result) so the API can report a parse failure rather than
        # hanging until the 30s timeout.
        content = (
            f"{ROUTING_RESPONSE_MARKER} id=xyz]\n"
            "I don't know how to format JSON sorry"
        )
        out = try_parse_routing_response(content)
        assert out is not None
        rid, result = out
        assert rid == "xyz"
        assert not result.ok

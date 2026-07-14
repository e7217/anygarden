"""Unit tests for speaker attribution & self-identity injection (#538).

멀티에이전트 룸에서 에이전트가 화자를 구분하지 못하는 결함 수정:
- ambient breadcrumb / addressed 메시지에 화자 이름·kind 라벨
- 엔진 프롬프트에 자기정체성(이름) 주입

라벨은 sender/roster가 주어질 때만 적용되어야 하며, 인자가 없으면
pre-#538 동작과 byte-identical 이어야 한다(하위호환).
"""

from __future__ import annotations

from typing import Any

from anygarden_agent.cli import _compose_identity_header, _with_identity
from anygarden_agent.coordination.pending_context import (
    format_context_line,
    resolve_speaker_label,
)
from anygarden_agent.integrations.base import EngineAdapter

ROSTER: dict[str, Any] = {
    "pid-01": {"display_name": "테스트에이전트01", "kind": "agent"},
    "pid-hu": {"display_name": "admin", "kind": "user"},
}


class TestResolveSpeakerLabel:
    def test_roster_hit_returns_name_and_kind(self) -> None:
        assert resolve_speaker_label("pid-01", ROSTER) == "테스트에이전트01(agent)"
        assert resolve_speaker_label("pid-hu", ROSTER) == "admin(user)"

    def test_miss_or_no_roster_returns_none(self) -> None:
        assert resolve_speaker_label("ghost", ROSTER) is None
        assert resolve_speaker_label("pid-01", None) is None
        assert resolve_speaker_label(None, ROSTER) is None


class TestFormatContextLine:
    def test_labels_with_name_when_roster_known(self) -> None:
        msg = {"content": "hi", "participant_id": "pid-01"}
        assert format_context_line(msg, roster=ROSTER) == "[참고] 테스트에이전트01(agent): hi"

    def test_falls_back_to_truncated_id_without_roster(self) -> None:
        # pre-#538 backward-compatible behaviour
        msg = {"content": "hi", "participant_id": "0123456789abcdef"}
        assert format_context_line(msg) == "[참고] @01234567: hi"

    def test_unknown_participant_falls_back_even_with_roster(self) -> None:
        msg = {"content": "hi", "participant_id": "ghostpid"}
        assert format_context_line(msg, roster=ROSTER) == "[참고] @ghostpid: hi"

    def test_empty_content_returns_none(self) -> None:
        assert (
            format_context_line({"content": "  ", "participant_id": "pid-01"}, roster=ROSTER)
            is None
        )


class _RosterAdapter(EngineAdapter):
    """Minimal adapter exposing a room roster via a stub client."""

    def __init__(self, roster_by_room: dict[str, Any]) -> None:
        self._pending_context: dict[str, list[tuple[float, str]]] = {}

        class _StubClient:
            pass

        client = _StubClient()
        client._participants_by_room = roster_by_room  # type: ignore[attr-defined]
        self._client = client

    async def on_message(self, msg: dict[str, Any]) -> str | None:
        return None

    async def start(self) -> None:
        return None


class TestAssembleUserContentSenderLabel:
    def test_addressed_message_prefixed_with_sender(self) -> None:
        adapter = _RosterAdapter({"r1": ROSTER})
        out = adapter.assemble_user_content("r1", "hello", sender_participant_id="pid-hu")
        assert out == "admin(user): hello"

    def test_no_sender_is_byte_identical(self) -> None:
        adapter = _RosterAdapter({"r1": ROSTER})
        assert adapter.assemble_user_content("r1", "hello") == "hello"

    def test_unknown_sender_not_labeled(self) -> None:
        adapter = _RosterAdapter({"r1": ROSTER})
        assert (
            adapter.assemble_user_content("r1", "hello", sender_participant_id="ghost")
            == "hello"
        )


class TestIdentityHeader:
    def test_header_contains_name(self) -> None:
        header = _compose_identity_header("테스트에이전트01")
        assert header is not None
        assert "테스트에이전트01" in header
        assert "participant" in header.lower()

    def test_with_identity_prepends_before_system_prompt(self) -> None:
        out = _with_identity("A", "You are helpful.")
        assert out is not None
        assert out.startswith('You are "A"')
        assert out.rstrip().endswith("You are helpful.")

    def test_with_identity_no_name_returns_prompt_unchanged(self) -> None:
        assert _with_identity(None, "sp") == "sp"

    def test_with_identity_none_prompt_returns_header_only(self) -> None:
        out = _with_identity("A", None)
        assert out is not None
        assert out.startswith('You are "A"')

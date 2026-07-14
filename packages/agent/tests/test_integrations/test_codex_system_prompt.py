"""codex-cli가 system_prompt(자기정체성 포함)를 turn content로 주입하는지 검증 (#540).

#538은 identity 헤더를 ``_system_prompt``에 넣었으나 codex_cli가 이를 읽지
않아 codex에 미도달했다. #540은 ``ShaTrackedInjector``에 system 블록을 추가하고
codex 어댑터가 이를 (is_collaborative 무관) 주입하도록 한다. system 블록은 첫
턴에 1회 방출되고, 변경 없으면 이후 턴에서 억제된다(codex resume가 히스토리에
보존하므로 재-paste 방지).
"""

from __future__ import annotations

import pytest

from anygarden_agent.integrations.base import ShaTrackedInjector
from anygarden_agent.integrations.codex_cli import CodexCliAdapter


class TestInjectorSystemBlock:
    def test_system_emitted_first_without_label(self) -> None:
        injector = ShaTrackedInjector()
        out = injector.apply(
            "r1",
            system_suffix="SYS",
            memory_suffix="MEM",
            roster_suffix="ROS",
            system_label="[sys]",
            memory_label="[mem]",
            roster_label="[ros]",
        )
        assert "SYS" in out and "MEM" in out and "ROS" in out
        assert "[sys]" not in out
        # system before memory before roster
        assert out.index("SYS") < out.index("MEM") < out.index("ROS")

    def test_system_suppressed_when_unchanged(self) -> None:
        injector = ShaTrackedInjector()
        kw = dict(
            memory_suffix="",
            roster_suffix="",
            system_label="[s]",
            memory_label="[m]",
            roster_label="[r]",
        )
        injector.apply("r1", system_suffix="SYS", **kw)
        assert injector.apply("r1", system_suffix="SYS", **kw) == ""

    def test_system_change_reemits_with_label(self) -> None:
        injector = ShaTrackedInjector()
        kw = dict(
            memory_suffix="",
            roster_suffix="",
            system_label="[sys-update]",
            memory_label="[m]",
            roster_label="[r]",
        )
        injector.apply("r1", system_suffix="SYS-V1", **kw)
        out = injector.apply("r1", system_suffix="SYS-V2", **kw)
        assert "[sys-update]" in out and "SYS-V2" in out

    def test_no_system_suffix_is_backward_compatible(self) -> None:
        # Existing callers that omit system_suffix must be unaffected.
        injector = ShaTrackedInjector()
        out = injector.apply(
            "r1",
            memory_suffix="MEM",
            roster_suffix="",
            memory_label="[m]",
            roster_label="[r]",
        )
        assert out == "MEM"


class TestCodexSystemPromptInjection:
    @pytest.mark.asyncio
    async def test_system_prompt_reaches_first_turn_then_suppressed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = CodexCliAdapter(system_prompt='You are "테스트에이전트01".')
        # No client → memory/roster suffixes empty; isolates the system block.
        adapter._client = None
        # Bypass the binary-presence guard in on_message; _call_codex is stubbed.
        adapter._codex_path = "codex"

        captured: list[str] = []

        async def fake_call(prompt: str, room_id: str) -> str:
            captured.append(prompt)
            return "ok"

        monkeypatch.setattr(adapter, "_call_codex", fake_call)

        await adapter.on_message(
            {"room_id": "r1", "content": "안녕", "participant_id": "p-human"}
        )
        assert 'You are "테스트에이전트01".' in captured[0]
        assert "안녕" in captured[0]

        # Second turn in same room: system prompt already in codex history →
        # suppressed to avoid duplicate paste.
        await adapter.on_message(
            {"room_id": "r1", "content": "또 안녕", "participant_id": "p-human"}
        )
        assert 'You are "테스트에이전트01".' not in captured[1]
        assert "또 안녕" in captured[1]

"""#526 — 엔진 세션 스토어 + 어댑터 load/save 배선 테스트.

respawn 연속성의 메커니즘(프로세스1이 저장 → 프로세스2가 복원)을 unit 레벨로
검증한다. end-to-end resume(엔진이 실제로 스토어를 붙여 대화를 잇는 것)은 running
cluster+machine+agent+LLM이 필요해 라이브 검증 대상이다.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from anygarden_agent.integrations.codex_cli import CodexCliAdapter
from anygarden_agent.integrations.engine_session_store import (
    _STORE_FILENAME,
    load_sessions,
    save_sessions,
)


class TestEngineSessionStore:
    def test_roundtrip(self, tmp_path: Path) -> None:
        save_sessions(tmp_path, {"room-a": "thread-1", "room-b": "sess-2"})
        assert load_sessions(tmp_path) == {"room-a": "thread-1", "room-b": "sess-2"}

    def test_absent_file_is_empty(self, tmp_path: Path) -> None:
        assert load_sessions(tmp_path) == {}

    def test_corrupt_file_is_empty(self, tmp_path: Path) -> None:
        (tmp_path / _STORE_FILENAME).write_text("{not valid json", encoding="utf-8")
        assert load_sessions(tmp_path) == {}

    def test_non_dict_json_is_empty(self, tmp_path: Path) -> None:
        (tmp_path / _STORE_FILENAME).write_text("[1, 2, 3]", encoding="utf-8")
        assert load_sessions(tmp_path) == {}

    def test_non_string_or_empty_values_filtered(self, tmp_path: Path) -> None:
        (tmp_path / _STORE_FILENAME).write_text(
            json.dumps({"a": "ok", "b": 5, "c": "", "d": None}), encoding="utf-8"
        )
        assert load_sessions(tmp_path) == {"a": "ok"}

    def test_save_leaves_no_temp_file(self, tmp_path: Path) -> None:
        save_sessions(tmp_path, {"r": "t"})
        assert not (tmp_path / f"{_STORE_FILENAME}.tmp").exists()
        assert not (tmp_path / ".anygarden-engine-sessions.json.tmp").exists()


class TestCodexAdapterPersistsSessions:
    @pytest.mark.asyncio
    async def test_start_restores_persisted_thread_ids(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A prior process persisted a resume handle for this agent cwd.
        save_sessions(tmp_path, {"room-x": "thread-abc"})
        monkeypatch.chdir(tmp_path)

        adapter = CodexCliAdapter(model="gpt-5.5")
        await adapter.start()
        # #526 — the respawned adapter restores the mapping (not a cold start).
        assert adapter._room_thread_ids == {"room-x": "thread-abc"}

    @pytest.mark.asyncio
    async def test_turn_persists_thread_id_and_survives_respawn(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)

        adapter = CodexCliAdapter(model="gpt-5.5")
        await adapter.start()
        assert adapter._room_thread_ids == {}

        async def fake_exec_once(prompt: str, thread_id: str | None):  # noqa: ARG001
            return ("reply", "thread-new", None, False)

        monkeypatch.setattr(adapter, "_exec_once", fake_exec_once)
        await adapter._call_codex("hello", "room-y")

        # Persisted to disk under the agent cwd...
        assert load_sessions(tmp_path) == {"room-y": "thread-new"}
        # ...and a fresh (respawned) adapter in the same cwd picks it up.
        respawned = CodexCliAdapter(model="gpt-5.5")
        await respawned.start()
        assert respawned._room_thread_ids == {"room-y": "thread-new"}

"""Durable per-room seq cursors + the stale catch-up guard.

A respawned agent used to reconnect with ``since_seq=0``, so every
mention that arrived while it was down was never delivered. Persisting
the cursor makes the server replay the gap; the guard keeps a long
outage from turning that replay into a burst of stale replies.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

from anygarden_agent.client import ChatClient
from anygarden_agent.integrations.base import MessagePolicy, decide_policy
from anygarden_agent.room_cursor_store import load_cursors, save_cursors


def _iso(age_seconds: float) -> str:
    return (
        datetime.now(UTC) - timedelta(seconds=age_seconds)
    ).isoformat()


class TestRoomCursorStore:
    def test_roundtrip(self, tmp_path: Path) -> None:
        save_cursors(tmp_path, {"room-1": 42})
        assert load_cursors(tmp_path) == {"room-1": 42}

    def test_absent_file_is_empty(self, tmp_path: Path) -> None:
        assert load_cursors(tmp_path) == {}

    def test_corrupt_file_is_empty(self, tmp_path: Path) -> None:
        (tmp_path / ".anygarden-room-cursors.json").write_text("{not json")
        assert load_cursors(tmp_path) == {}

    def test_non_int_values_are_dropped(self, tmp_path: Path) -> None:
        (tmp_path / ".anygarden-room-cursors.json").write_text(
            '{"room-1": "x", "room-2": 7}'
        )
        assert load_cursors(tmp_path) == {"room-2": 7}

    def test_save_leaves_no_temp_file(self, tmp_path: Path) -> None:
        save_cursors(tmp_path, {"room-1": 1})
        assert [p.name for p in tmp_path.iterdir()] == [
            ".anygarden-room-cursors.json"
        ]


class TestClientCursorPersistence:
    def test_cursor_survives_a_respawn(self, tmp_path: Path) -> None:
        client = ChatClient(
            "ws://localhost:8000", token="t", agent_name="bot", state_dir=tmp_path
        )
        client._note_seq("room-1", 17)

        respawned = ChatClient(
            "ws://localhost:8000", token="t", agent_name="bot", state_dir=tmp_path
        )

        assert respawned._last_seq["room-1"] == 17

    def test_without_a_state_dir_nothing_is_written(self, tmp_path: Path) -> None:
        """The plain text client and tests must not litter their cwd."""
        client = ChatClient("ws://localhost:8000", token="t", agent_name="bot")
        client._note_seq("room-1", 17)

        assert client._last_seq["room-1"] == 17
        assert list(tmp_path.iterdir()) == []

    def test_cursor_never_moves_backwards(self, tmp_path: Path) -> None:
        client = ChatClient(
            "ws://localhost:8000", token="t", agent_name="bot", state_dir=tmp_path
        )
        client._note_seq("room-1", 17)
        client._note_seq("room-1", 4)

        assert load_cursors(tmp_path)["room-1"] == 17


def _make_client():
    client = MagicMock()
    client._agent_name = "테스트에이전트"
    client._my_participant_ids = {"my-pid-123"}
    client._agent_id = None
    client._context_window_opt_out = False
    client._recent_msgs = {}
    client._speaker_strategy = {}
    client._orchestrator_agent_id = {}
    return client


class TestStaleCatchupGuard:
    def _mention_msg(self, created_at: str | None) -> dict:
        msg = {
            "participant_id": "other-pid",
            "content": "<@user:my-pid-123> 확인 부탁합니다",
            "metadata": {
                "mentions": [{"type": "user", "id": "my-pid-123"}],
                "wake_trigger": "mention",
            },
        }
        if created_at is not None:
            msg["created_at"] = created_at
        return msg

    def test_recent_mention_still_wakes_the_agent(self) -> None:
        policy = decide_policy(self._mention_msg(_iso(5)), _make_client())
        assert policy is MessagePolicy.RESPOND

    def test_stale_mention_is_ingested_instead_of_answered(self) -> None:
        """Replayed after an outage: the agent absorbs the context but
        does not reply to an hours-old request."""
        policy = decide_policy(self._mention_msg(_iso(6 * 3600)), _make_client())
        assert policy is MessagePolicy.INGEST_ONLY

    def test_message_without_a_timestamp_keeps_legacy_behaviour(self) -> None:
        policy = decide_policy(self._mention_msg(None), _make_client())
        assert policy is MessagePolicy.RESPOND

    def test_own_stale_message_is_still_skipped(self) -> None:
        msg = self._mention_msg(_iso(6 * 3600))
        msg["participant_id"] = "my-pid-123"
        assert decide_policy(msg, _make_client()) is MessagePolicy.SKIP

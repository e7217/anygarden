"""Unit tests for the client-side per-room caches fed by ``welcome`` and
``room_settings_changed`` (#221, #237, #644)."""

from __future__ import annotations

import pytest

from anygarden_agent.client import ChatClient


class TestParticipantsRosterCache:
    """Issue #221 — welcome stamps a participants roster so the adapter
    can inject valid participant ids into its LLM prompt instead of
    guessing display names."""

    def _make_client(self) -> ChatClient:
        return ChatClient("ws://x", token="t")

    def test_initial_roster_is_empty(self) -> None:
        client = self._make_client()
        assert client._participants_by_room == {}

    @pytest.mark.asyncio
    async def test_welcome_populates_roster(self) -> None:
        client = self._make_client()
        await client._process_frame(
            "room-a",
            {
                "type": "welcome",
                "participant_id": "p1",
                "participants": [
                    {"id": "p1", "display_name": "me", "kind": "agent", "agent_id": "A1"},
                    {"id": "p2", "display_name": "bob", "kind": "user", "agent_id": None},
                ],
            },
        )
        roster = client._participants_by_room["room-a"]
        assert set(roster.keys()) == {"p1", "p2"}
        assert roster["p2"]["display_name"] == "bob"
        assert roster["p2"]["kind"] == "user"
        assert roster["p1"]["agent_id"] == "A1"

    @pytest.mark.asyncio
    async def test_welcome_without_participants_caches_empty_dict(self) -> None:
        """Older servers omit the field — cache an empty mapping so
        the adapter can iterate without a KeyError when a pre-#221
        server hasn't been upgraded yet."""
        client = self._make_client()
        await client._process_frame(
            "room-a",
            {"type": "welcome", "participant_id": "p1"},
        )
        assert client._participants_by_room["room-a"] == {}


class TestRoomSettingsChangedFrame:
    """Issue #221 — ``room_settings_changed`` refreshes cached settings so
    admin PATCHes propagate without a reconnection."""

    def _make_client(self) -> ChatClient:
        return ChatClient("ws://x", token="t")

    @pytest.mark.asyncio
    async def test_frame_updates_ephemeral(self) -> None:
        client = self._make_client()
        await client._process_frame(
            "room-a", {"type": "welcome", "participant_id": "p1", "ephemeral": False}
        )
        await client._process_frame(
            "room-a",
            {"type": "room_settings_changed", "room_id": "room-a", "ephemeral": True},
        )
        assert client._room_ephemeral["room-a"] is True

    @pytest.mark.asyncio
    async def test_frame_with_none_fields_preserves_cache(self) -> None:
        """``None`` means "not touched by this PATCH"."""
        client = self._make_client()
        await client._process_frame(
            "room-a", {"type": "welcome", "participant_id": "p1", "ephemeral": True}
        )
        await client._process_frame(
            "room-a",
            {
                "type": "room_settings_changed",
                "room_id": "room-a",
                "ephemeral": None,
                "context_window_enabled": False,
            },
        )
        assert client._room_ephemeral["room-a"] is True


class TestRosterRefreshFrame:
    """Issue #644 — ``room_settings_changed`` also carries a full
    participant snapshot so a membership change or a ``description``
    edit reaches connected agents.

    Pre-#644 the roster was welcome-only: an agent that stayed
    connected kept rendering a roster frozen at connect time, so it
    could neither see a newcomer nor stop addressing someone who had
    left, and an updated introduction — the LLM's only basis for
    picking whom to ask — was ignored entirely.
    """

    def _make_client(self) -> ChatClient:
        return ChatClient("ws://x", token="t")

    async def _seed(self, client: ChatClient) -> None:
        await client._process_frame(
            "room-a",
            {
                "type": "welcome",
                "participant_id": "p-self",
                "participants": [
                    {"id": "p-self", "display_name": "me", "kind": "agent"},
                    {
                        "id": "p-old",
                        "display_name": "old-bot",
                        "kind": "agent",
                        "description": "이전 소개문",
                    },
                ],
            },
        )

    @pytest.mark.asyncio
    async def test_frame_replaces_roster_snapshot(self) -> None:
        client = self._make_client()
        await self._seed(client)
        await client._process_frame(
            "room-a",
            {
                "type": "room_settings_changed",
                "room_id": "room-a",
                "participants": [
                    {"id": "p-self", "display_name": "me", "kind": "agent"},
                    {
                        "id": "p-new",
                        "display_name": "new-bot",
                        "kind": "agent",
                        "description": "새 소개문",
                    },
                ],
            },
        )
        roster = client._participants_by_room["room-a"]
        # Wholesale replacement, not a merge: the departed peer is gone
        # and the newcomer is present.
        assert set(roster) == {"p-self", "p-new"}
        assert roster["p-new"]["description"] == "새 소개문"

    @pytest.mark.asyncio
    async def test_refreshed_roster_reaches_the_prompt(self) -> None:
        """The cache is only useful if the rendered suffix follows it —
        this is the behaviour the whole change exists for."""
        client = self._make_client()
        await self._seed(client)
        client._my_participant_ids.add("p-self")
        assert "old-bot" in client.compose_roster_suffix("room-a")

        await client._process_frame(
            "room-a",
            {
                "type": "room_settings_changed",
                "room_id": "room-a",
                "participants": [
                    {"id": "p-self", "display_name": "me", "kind": "agent"},
                    {
                        "id": "p-new",
                        "display_name": "new-bot",
                        "kind": "agent",
                        "description": "새 소개문",
                    },
                ],
            },
        )
        suffix = client.compose_roster_suffix("room-a")
        assert "Current room ID: room-a" in suffix
        assert "participant IDs below are only for assignee_pid" in suffix
        assert "routing token in the final reply" in suffix
        assert "new-bot" in suffix
        assert "새 소개문" in suffix
        assert "old-bot" not in suffix

    @pytest.mark.asyncio
    async def test_settings_only_frame_preserves_roster(self) -> None:
        """A settings PATCH omits ``participants``; that must not be
        read as "the room emptied" — same "None = not touched" rule
        the other fields follow."""
        client = self._make_client()
        await self._seed(client)
        await client._process_frame(
            "room-a",
            {
                "type": "room_settings_changed",
                "room_id": "room-a",
                "ephemeral": True,
            },
        )
        assert set(client._participants_by_room["room-a"]) == {"p-self", "p-old"}
        assert client._room_ephemeral["room-a"] is True

    @pytest.mark.asyncio
    async def test_malformed_entries_are_skipped(self) -> None:
        """Mirrors the welcome-path guard: entries without an ``id``
        (or that aren't dicts at all) must not land in the cache and
        must not abort the refresh for the well-formed ones."""
        client = self._make_client()
        await self._seed(client)
        await client._process_frame(
            "room-a",
            {
                "type": "room_settings_changed",
                "room_id": "room-a",
                "participants": [
                    "not-a-dict",
                    {"display_name": "no-id"},
                    {"id": "p-ok", "display_name": "ok-bot", "kind": "agent"},
                ],
            },
        )
        assert set(client._participants_by_room["room-a"]) == {"p-ok"}

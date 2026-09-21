"""Unit tests for client-side speaker-strategy cache (#159 Phase A)."""

from __future__ import annotations

import pytest

from anygarden_agent.client import ChatClient


class TestSpeakerStrategyCache:
    """Welcome frames populate per-room strategy caches; defaults
    preserve the pre-#159 behaviour when the server hasn't sent the
    new fields."""

    def _make_client(self) -> ChatClient:
        return ChatClient("ws://x", token="t")

    def test_initial_caches_are_empty(self) -> None:
        client = self._make_client()
        assert client._speaker_strategy == {}
        assert client._orchestrator_agent_id == {}
        assert client._next_speaker_participant_id == {}

    @pytest.mark.asyncio
    async def test_welcome_populates_defaults_without_new_fields(self) -> None:
        """Older servers omit the #159 fields — defaults apply."""
        client = self._make_client()
        await client._process_frame(
            "room-a",
            {
                "type": "welcome",
                "participant_id": "p1",
            },
        )
        assert client._speaker_strategy["room-a"] == "mentioned_only"
        assert client._orchestrator_agent_id["room-a"] is None
        assert client._next_speaker_participant_id["room-a"] is None

    @pytest.mark.asyncio
    async def test_welcome_propagates_explicit_fields(self) -> None:
        client = self._make_client()
        await client._process_frame(
            "room-a",
            {
                "type": "welcome",
                "participant_id": "p1",
                "speaker_strategy": "orchestrator",
                "orchestrator_agent_id": "agent-ABC",
                "next_speaker_participant_id": "part-XYZ",
            },
        )
        assert client._speaker_strategy["room-a"] == "orchestrator"
        assert client._orchestrator_agent_id["room-a"] == "agent-ABC"
        assert client._next_speaker_participant_id["room-a"] == "part-XYZ"

    @pytest.mark.asyncio
    async def test_per_room_isolation(self) -> None:
        """Two rooms can hold distinct strategies."""
        client = self._make_client()
        await client._process_frame(
            "room-a",
            {
                "type": "welcome",
                "participant_id": "p1",
                "speaker_strategy": "round_robin",
            },
        )
        await client._process_frame(
            "room-b",
            {
                "type": "welcome",
                "participant_id": "p1",
                "speaker_strategy": "orchestrator",
                "orchestrator_agent_id": "agent-B",
            },
        )
        assert client._speaker_strategy["room-a"] == "round_robin"
        assert client._speaker_strategy["room-b"] == "orchestrator"
        assert client._orchestrator_agent_id["room-a"] is None
        assert client._orchestrator_agent_id["room-b"] == "agent-B"

    @pytest.mark.asyncio
    async def test_welcome_refreshes_cache(self) -> None:
        """A second welcome overwrites the cached values (admin toggle + respawn)."""
        client = self._make_client()
        await client._process_frame(
            "room-a",
            {
                "type": "welcome",
                "participant_id": "p1",
                "speaker_strategy": "orchestrator",
                "next_speaker_participant_id": "part-OLD",
            },
        )
        await client._process_frame(
            "room-a",
            {
                "type": "welcome",
                "participant_id": "p1",
                "speaker_strategy": "orchestrator",
                "next_speaker_participant_id": "part-NEW",
            },
        )
        assert client._next_speaker_participant_id["room-a"] == "part-NEW"


class TestParticipantsRosterCache:
    """Issue #221 — welcome stamps a participants roster so the
    orchestrator adapter can inject valid UUIDs into its ``handoff_to``
    LLM prompt instead of guessing display names."""

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
    """Issue #221 — ``room_settings_changed`` refreshes cached dispatch
    fields on the fly so admin PATCHes propagate without a
    reconnection. Before this frame existed, the settings lived only
    in ``welcome`` so a mid-session change silently left connected
    agents on the old strategy."""

    def _make_client(self) -> ChatClient:
        return ChatClient("ws://x", token="t")

    @pytest.mark.asyncio
    async def test_frame_updates_speaker_strategy(self) -> None:
        client = self._make_client()
        # Seed caches from the initial welcome.
        await client._process_frame(
            "room-a",
            {
                "type": "welcome",
                "participant_id": "p1",
                "speaker_strategy": "mentioned_only",
            },
        )
        await client._process_frame(
            "room-a",
            {
                "type": "room_settings_changed",
                "room_id": "room-a",
                "speaker_strategy": "orchestrator",
                "orchestrator_agent_id": "A1",
            },
        )
        assert client._speaker_strategy["room-a"] == "orchestrator"
        assert client._orchestrator_agent_id["room-a"] == "A1"

    @pytest.mark.asyncio
    async def test_frame_with_none_fields_preserves_cache(self) -> None:
        """``None`` means "not touched by this PATCH" — only non-None
        fields overwrite cached values. Mirrors the server's partial
        update semantics."""
        client = self._make_client()
        await client._process_frame(
            "room-a",
            {
                "type": "welcome",
                "participant_id": "p1",
                "speaker_strategy": "orchestrator",
                "orchestrator_agent_id": "A1",
            },
        )
        await client._process_frame(
            "room-a",
            {
                "type": "room_settings_changed",
                "room_id": "room-a",
                "speaker_strategy": None,
                "orchestrator_agent_id": "A2",
            },
        )
        # speaker_strategy untouched; only orchestrator_agent_id rolls forward.
        assert client._speaker_strategy["room-a"] == "orchestrator"
        assert client._orchestrator_agent_id["room-a"] == "A2"


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
                "speaker_strategy": "orchestrator",
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
                "speaker_strategy": "mentioned_only",
            },
        )
        assert set(client._participants_by_room["room-a"]) == {"p-self", "p-old"}
        assert client._speaker_strategy["room-a"] == "mentioned_only"

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

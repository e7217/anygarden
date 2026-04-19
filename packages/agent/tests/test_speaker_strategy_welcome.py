"""Unit tests for client-side speaker-strategy cache (#159 Phase A)."""

from __future__ import annotations

import pytest

from doorae_agent.client import ChatClient


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

"""Unit tests for the ``ask_peer`` scheduling and checks (#737)."""

from __future__ import annotations

import pytest

from anygarden.db.models import (
    Agent,
    AgentTurn,
    Message,
    Participant,
    Project,
    Room,
    User,
)
from anygarden.orchestration.peer_ask import (
    PeerAsk,
    PendingPeerAsks,
    check_peer_ask,
)
from anygarden.orchestration.rules import PeerHandoffBudget


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _ask(target: str, request_id: str | None = "r1", at: float = 1000.0) -> PeerAsk:
    return PeerAsk(target_pid=target, question=f"q-{target}", request_id=request_id, created_at=at)


class TestPendingPeerAsks:
    def test_take_returns_asks_bound_to_the_reply_turn(self) -> None:
        store = PendingPeerAsks(clock=_Clock())
        store.schedule("a", "room", _ask("b", "r1"))
        store.schedule("a", "room", _ask("c", "r2"))

        assert [x.target_pid for x in store.take("a", "room", request_id="r1")] == ["b"]
        assert [x.target_pid for x in store.pending("a", "room")] == ["c"]

    def test_unbound_ask_rides_any_reply(self) -> None:
        store = PendingPeerAsks(clock=_Clock())
        store.schedule("a", "room", _ask("b", None))

        assert [x.target_pid for x in store.take("a", "room", request_id=None)] == ["b"]
        assert store.pending("a", "room") == []

    def test_newer_ask_to_same_target_replaces_older(self) -> None:
        store = PendingPeerAsks(clock=_Clock())
        store.schedule("a", "room", _ask("b"))
        store.schedule("a", "room", PeerAsk("b", "newer", "r1", 1000.0))

        taken = store.take("a", "room", request_id="r1")
        assert [(x.target_pid, x.question) for x in taken] == [("b", "newer")]

    def test_expired_asks_are_dropped(self) -> None:
        clock = _Clock()
        store = PendingPeerAsks(ttl_seconds=60, clock=clock)
        store.schedule("a", "room", _ask("b"))
        clock.t += 61

        assert store.take("a", "room", request_id="r1") == []

    def test_clear_room_only_touches_that_room(self) -> None:
        store = PendingPeerAsks(clock=_Clock())
        store.schedule("a", "room", _ask("b"))
        store.schedule("a", "other", _ask("c"))

        assert [x.target_pid for x in store.clear_room("room")] == ["b"]
        assert [x.target_pid for x in store.pending("a", "other")] == ["c"]


class TestWouldBlock:
    def test_first_handoff_passes_then_depth_blocks(self) -> None:
        budget = PeerHandoffBudget()
        assert budget.would_block("r") is False
        budget.consume("r")
        assert budget.would_block("r") is True

    def test_empty_budget_blocks(self) -> None:
        budget = PeerHandoffBudget(capacity=0)
        assert budget.would_block("r") is True


async def _seed(db) -> dict[str, str]:
    user = User(email="h@test.com", password_hash="x", display_name="admin")
    project = Project(name="p")
    db.add_all([user, project])
    await db.flush()
    room = Room(project_id=project.id, name="r")
    caller = Agent(name="PM", engine="codex")
    peer = Agent(name="local-agent", engine="codex")
    db.add_all([room, caller, peer])
    await db.flush()
    human_p = Participant(room_id=room.id, user_id=user.id, role="member")
    caller_p = Participant(room_id=room.id, agent_id=caller.id, role="member")
    peer_p = Participant(room_id=room.id, agent_id=peer.id, role="member")
    db.add_all([human_p, caller_p, peer_p])
    await db.flush()
    trigger = Message(room_id=room.id, participant_id=human_p.id, content="@everyone 소개", seq=1)
    db.add(trigger)
    await db.flush()
    db.add_all([
        Message(room_id=room.id, participant_id=peer_p.id, content="local-agent 소개", seq=2),
        Message(room_id=room.id, participant_id=human_p.id, content="thread", seq=3,
                parent_message_id=trigger.id, root_message_id=trigger.id),
    ])
    db.add(AgentTurn(
        request_id="turn-1",
        room_id=room.id,
        target_participant_id=caller_p.id,
        agent_id=caller.id,
        trigger_message_id=trigger.id,
        idempotency_key="k1",
        state="leased",
    ))
    await db.commit()
    return {
        "room": room.id, "caller": caller.id, "caller_p": caller_p.id,
        "peer_p": peer_p.id, "human_p": human_p.id,
    }


class TestCheckPeerAsk:
    @pytest.mark.asyncio
    async def test_schedulable_peer_is_bound_to_the_open_turn(self, db) -> None:
        ids = await _seed(db)
        decision = await check_peer_ask(
            db, budget=PeerHandoffBudget(), agent_id=ids["caller"],
            room_id=ids["room"], target_pid=ids["peer_p"],
        )

        assert decision.status == "scheduled"
        assert decision.request_id == "turn-1"
        assert decision.target_name == "local-agent"
        assert decision.since_turn_start == []

    @pytest.mark.asyncio
    async def test_already_woken_peer_is_rejected_with_recent_messages(self, db) -> None:
        ids = await _seed(db)
        budget = PeerHandoffBudget()
        budget.mark_woken(ids["room"], [ids["caller_p"], ids["peer_p"]])

        decision = await check_peer_ask(
            db, budget=budget, agent_id=ids["caller"],
            room_id=ids["room"], target_pid=ids["peer_p"],
        )

        assert decision.status == "rejected"
        assert decision.reason == "already_answering"
        # Root-level messages after the trigger only; the thread reply is not
        # part of the caller's conversation.
        assert decision.since_turn_start == [
            {"seq": 2, "speaker": "local-agent", "content": "local-agent 소개"}
        ]

    @pytest.mark.asyncio
    async def test_exhausted_depth_is_rejected(self, db) -> None:
        ids = await _seed(db)
        budget = PeerHandoffBudget()
        budget.consume(ids["room"])

        decision = await check_peer_ask(
            db, budget=budget, agent_id=ids["caller"],
            room_id=ids["room"], target_pid=ids["peer_p"],
        )

        assert decision.status == "rejected"
        assert decision.reason == "limit_reached"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("which,needle", [
        ("human_p", "only calls agents"),
        ("caller_p", "yourself"),
        ("missing", "not a participant"),
    ])
    async def test_invalid_targets(self, db, which: str, needle: str) -> None:
        ids = await _seed(db)
        target = ids.get(which, "no-such-participant")

        decision = await check_peer_ask(
            db, budget=PeerHandoffBudget(), agent_id=ids["caller"],
            room_id=ids["room"], target_pid=target,
        )

        assert decision.status == "invalid"
        assert needle in (decision.detail or "")

    @pytest.mark.asyncio
    async def test_caller_outside_the_room_is_invalid(self, db) -> None:
        ids = await _seed(db)
        decision = await check_peer_ask(
            db, budget=None, agent_id="stranger",
            room_id=ids["room"], target_pid=ids["peer_p"],
        )
        assert decision.status == "invalid"

    @pytest.mark.asyncio
    async def test_no_open_turn_means_unbound_and_no_history(self, db) -> None:
        ids = await _seed(db)
        turn = await db.get(AgentTurn, "turn-1")
        turn.state = "completed"
        await db.commit()
        budget = PeerHandoffBudget()
        budget.mark_woken(ids["room"], [ids["peer_p"]])

        decision = await check_peer_ask(
            db, budget=budget, agent_id=ids["caller"],
            room_id=ids["room"], target_pid=ids["peer_p"],
        )

        assert decision.status == "rejected"
        assert decision.request_id is None
        assert decision.since_turn_start == []

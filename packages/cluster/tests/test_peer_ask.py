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
    sender_hop,
    turn_hop,
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


class TestPendingInRoom:
    def test_counts_every_callers_asks_in_the_room(self) -> None:
        store = PendingPeerAsks(clock=_Clock())
        store.schedule("a", "room", _ask("b"))
        store.schedule("c", "room", _ask("d"))
        store.schedule("a", "other", _ask("e"))

        assert sorted((agent, x.target_pid) for agent, x in store.pending_in_room("room")) == [
            ("a", "b"), ("c", "d"),
        ]


class TestWouldBlock:
    """#756 — depth comes from the caller's hop, the budget from head count."""

    def test_first_hop_passes_while_slots_remain(self) -> None:
        budget = PeerHandoffBudget()
        assert budget.would_block("r") is False
        # Earlier calls in the same user turn no longer count as depth.
        budget.consume("r")
        assert budget.would_block("r") is False

    def test_second_hop_is_blocked(self) -> None:
        assert PeerHandoffBudget().would_block("r", hop=2) is True

    def test_reserved_asks_hold_their_slots(self) -> None:
        budget = PeerHandoffBudget(capacity=2)
        assert budget.would_block("r", reserved=1) is False
        assert budget.would_block("r", reserved=2) is True

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


async def _add_agent(db, room_id: str, name: str) -> str:
    agent = Agent(name=name, engine="codex")
    db.add(agent)
    await db.flush()
    part = Participant(room_id=room_id, agent_id=agent.id, role="member")
    db.add(part)
    await db.commit()
    return part.id


async def _retrigger(db, ids: dict[str, str], *, author_pid: str, metadata: dict | None) -> None:
    """Point the caller's open turn at a new trigger message."""
    trigger = Message(
        room_id=ids["room"], participant_id=author_pid, content="trigger",
        seq=10, extra_metadata=metadata,
    )
    db.add(trigger)
    await db.flush()
    turn = await db.get(AgentTurn, "turn-1")
    turn.trigger_message_id = trigger.id
    await db.commit()


class TestTurnHop:
    """#756 — a turn is hop 2 only when a peer's call started it."""

    @pytest.mark.asyncio
    async def test_turn_started_by_a_person_is_hop_one(self, db) -> None:
        await _seed(db)
        assert await turn_hop(db, await db.get(AgentTurn, "turn-1")) == 1

    @pytest.mark.asyncio
    async def test_no_turn_is_hop_one(self, db) -> None:
        assert await turn_hop(db, None) == 1

    @pytest.mark.asyncio
    async def test_turn_started_by_a_peer_mention_is_hop_two(self, db) -> None:
        ids = await _seed(db)
        await _retrigger(db, ids, author_pid=ids["peer_p"],
                         metadata={"mentions": [{"type": "user", "id": ids["caller_p"]}]})
        assert await turn_hop(db, await db.get(AgentTurn, "turn-1")) == 2

    @pytest.mark.asyncio
    async def test_agent_message_without_a_call_is_hop_one(self, db) -> None:
        """Round-robin / handoff nominations wake an agent without calling it."""
        ids = await _seed(db)
        await _retrigger(db, ids, author_pid=ids["peer_p"], metadata=None)
        assert await turn_hop(db, await db.get(AgentTurn, "turn-1")) == 1

    @pytest.mark.asyncio
    async def test_delegation_is_hop_one(self, db) -> None:
        ids = await _seed(db)
        await _retrigger(db, ids, author_pid=ids["peer_p"], metadata={
            "mentions": [{"type": "user", "id": ids["caller_p"]}],
            "delegation_target_participant_id": ids["caller_p"],
            "delegation_id": "d-1",
        })
        assert await turn_hop(db, await db.get(AgentTurn, "turn-1")) == 1

    @pytest.mark.asyncio
    async def test_sender_hop_only_trusts_the_senders_own_turn(self, db) -> None:
        ids = await _seed(db)
        await _retrigger(db, ids, author_pid=ids["peer_p"],
                         metadata={"mentions": [{"type": "user", "id": ids["caller_p"]}]})
        assert await sender_hop(db, request_id="turn-1", participant_id=ids["caller_p"]) == 2
        # Another participant quoting this turn's id gains nothing from it.
        assert await sender_hop(db, request_id="turn-1", participant_id=ids["peer_p"]) == 1
        assert await sender_hop(db, request_id=None, participant_id=ids["caller_p"]) == 1


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
    async def test_exhausted_budget_is_rejected(self, db) -> None:
        ids = await _seed(db)
        budget = PeerHandoffBudget(capacity=1)
        budget.consume(ids["room"])

        decision = await check_peer_ask(
            db, budget=budget, agent_id=ids["caller"],
            room_id=ids["room"], target_pid=ids["peer_p"],
        )

        assert decision.status == "rejected"
        assert decision.reason == "limit_reached"

    @pytest.mark.asyncio
    async def test_two_targets_in_one_turn_are_both_scheduled(self, db) -> None:
        """#756 — asking several peers at once is one hop, not two."""
        ids = await _seed(db)
        other = await _add_agent(db, ids["room"], "agent01")
        budget, pending = PeerHandoffBudget(), PendingPeerAsks(clock=_Clock())

        for target in (ids["peer_p"], other):
            decision = await check_peer_ask(
                db, budget=budget, pending=pending, agent_id=ids["caller"],
                room_id=ids["room"], target_pid=target,
            )
            assert decision.status == "scheduled"
            pending.schedule(ids["caller"], ids["room"], _ask(target, "turn-1"))

    @pytest.mark.asyncio
    async def test_scheduled_asks_count_against_the_budget(self, db) -> None:
        """#756 — an ask the tool calls scheduled must still fit at send time."""
        ids = await _seed(db)
        other = await _add_agent(db, ids["room"], "agent01")
        budget, pending = PeerHandoffBudget(capacity=1), PendingPeerAsks(clock=_Clock())
        pending.schedule(ids["caller"], ids["room"], _ask(other, "turn-1"))

        decision = await check_peer_ask(
            db, budget=budget, pending=pending, agent_id=ids["caller"],
            room_id=ids["room"], target_pid=ids["peer_p"],
        )
        assert decision.status == "rejected"
        assert decision.reason == "limit_reached"

        # Re-asking a target already scheduled replaces that ask, so it
        # does not need a second slot.
        decision = await check_peer_ask(
            db, budget=budget, pending=pending, agent_id=ids["caller"],
            room_id=ids["room"], target_pid=other,
        )
        assert decision.status == "scheduled"

    @pytest.mark.asyncio
    async def test_peer_already_called_this_turn_is_already_answering(self, db) -> None:
        ids = await _seed(db)
        budget = PeerHandoffBudget()
        budget.mark_peer_called(ids["room"], [ids["peer_p"]])

        decision = await check_peer_ask(
            db, budget=budget, agent_id=ids["caller"],
            room_id=ids["room"], target_pid=ids["peer_p"],
        )

        assert decision.status == "rejected"
        assert decision.reason == "already_answering"

    @pytest.mark.asyncio
    async def test_turn_started_by_a_peer_call_is_depth_blocked(self, db) -> None:
        ids = await _seed(db)
        other = await _add_agent(db, ids["room"], "agent01")
        await _retrigger(db, ids, author_pid=ids["peer_p"],
                         metadata={"mentions": [{"type": "user", "id": ids["caller_p"]}]})

        decision = await check_peer_ask(
            db, budget=PeerHandoffBudget(), agent_id=ids["caller"],
            room_id=ids["room"], target_pid=other,
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

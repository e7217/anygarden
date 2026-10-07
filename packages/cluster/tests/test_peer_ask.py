"""Unit tests for the ``ask_peer`` checks (#737, #762)."""

from __future__ import annotations

import pytest

from anygarden.db.models import (
    Agent,
    AgentTurn,
    AgentTurnAttempt,
    Message,
    Participant,
    Project,
    Room,
    User,
)
from anygarden.orchestration.peer_ask import (
    check_peer_ask,
    resolve_caller,
    sender_hop,
    since_turn_start,
    turn_hop,
)
from anygarden.orchestration.rules import PeerHandoffBudget


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
        """An agent message that calls no one does not deepen the hop."""
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
    async def test_idle_peer_is_accepted_and_the_caller_is_bound_to_its_turn(self, db) -> None:
        ids = await _seed(db)
        caller = await resolve_caller(db, agent_id=ids["caller"], room_id=ids["room"])

        decision = await check_peer_ask(db, caller=caller, target_pid=ids["peer_p"])

        assert caller.turn.request_id == "turn-1"
        assert caller.hop == 1
        assert decision.status == "accepted"
        assert decision.target_name == "local-agent"

    @pytest.mark.asyncio
    async def test_peer_with_an_open_turn_is_already_answering(self, db) -> None:
        """#762 — "already answering" is an open turn in the room, not a
        memory of who was called this user turn."""
        ids = await _seed(db)
        db.add(AgentTurn(
            request_id="peer-turn", room_id=ids["room"], target_participant_id=ids["peer_p"],
            idempotency_key="k-peer", state="pending",
        ))
        await db.commit()
        caller = await resolve_caller(db, agent_id=ids["caller"], room_id=ids["room"])

        decision = await check_peer_ask(db, caller=caller, target_pid=ids["peer_p"])

        assert decision.status == "rejected"
        assert decision.reason == "already_answering"
        # Root-level messages after the trigger only; the thread reply is not
        # part of the caller's conversation.
        assert await since_turn_start(db, caller) == [
            {"seq": 2, "speaker": "local-agent", "content": "local-agent 소개"}
        ]

    @pytest.mark.asyncio
    async def test_peer_that_already_finished_can_be_asked_again(self, db) -> None:
        ids = await _seed(db)
        db.add(AgentTurn(
            request_id="peer-turn", room_id=ids["room"], target_participant_id=ids["peer_p"],
            idempotency_key="k-peer", state="completed",
        ))
        await db.commit()
        budget = PeerHandoffBudget()
        budget.mark_peer_called(ids["room"], [ids["peer_p"]])
        caller = await resolve_caller(db, agent_id=ids["caller"], room_id=ids["room"])

        decision = await check_peer_ask(db, caller=caller, target_pid=ids["peer_p"])

        assert decision.status == "accepted"

    @pytest.mark.asyncio
    async def test_turn_started_by_a_peer_call_is_hop_two(self, db) -> None:
        ids = await _seed(db)
        await _retrigger(db, ids, author_pid=ids["peer_p"],
                         metadata={"mentions": [{"type": "user", "id": ids["caller_p"]}]})

        caller = await resolve_caller(db, agent_id=ids["caller"], room_id=ids["room"])

        assert caller.hop == 2

    @pytest.mark.asyncio
    async def test_running_turn_is_preferred_over_a_newer_queued_one(self, db) -> None:
        """The MCP call carries no turn id; the executing turn wins."""
        ids = await _seed(db)
        db.add(AgentTurnAttempt(
            turn_id="turn-1", agent_id=ids["caller"], attempt_number=1,
            generation=0, lease_token="lease-1", state="started",
        ))
        db.add(AgentTurn(
            request_id="turn-2", room_id=ids["room"], target_participant_id=ids["caller_p"],
            agent_id=ids["caller"], idempotency_key="k2", state="pending",
        ))
        db.add(AgentTurnAttempt(
            turn_id="turn-2", agent_id=ids["caller"], attempt_number=1,
            generation=0, lease_token="lease-2", state="pending",
        ))
        await db.commit()

        caller = await resolve_caller(db, agent_id=ids["caller"], room_id=ids["room"])

        assert caller.turn.request_id == "turn-1"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("which,needle", [
        ("human_p", "only calls agents"),
        ("caller_p", "yourself"),
        ("missing", "not a participant"),
    ])
    async def test_invalid_targets(self, db, which: str, needle: str) -> None:
        ids = await _seed(db)
        caller = await resolve_caller(db, agent_id=ids["caller"], room_id=ids["room"])

        decision = await check_peer_ask(
            db, caller=caller, target_pid=ids.get(which, "no-such-participant")
        )

        assert decision.status == "invalid"
        assert needle in (decision.detail or "")

    @pytest.mark.asyncio
    async def test_caller_outside_the_room_is_none(self, db) -> None:
        ids = await _seed(db)
        assert await resolve_caller(db, agent_id="stranger", room_id=ids["room"]) is None

    @pytest.mark.asyncio
    async def test_no_open_turn_means_no_turn_and_no_history(self, db) -> None:
        ids = await _seed(db)
        turn = await db.get(AgentTurn, "turn-1")
        turn.state = "completed"
        await db.commit()

        caller = await resolve_caller(db, agent_id=ids["caller"], room_id=ids["room"])

        assert caller.turn is None
        assert await since_turn_start(db, caller) == []

"""What the server attaches to a delivered turn: the conversation the agent
missed since its last turn there (#866)."""

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
from anygarden.turns.conversation_context import (
    MAX_CONTEXT_MESSAGES,
    build_conversation_context,
)


class _Room:
    def __init__(self, db) -> None:
        self.db = db
        self.seq = 0
        self.turns = 0

    async def setup(self) -> None:
        user = User(email="h@test.com", password_hash="x", display_name="human")
        project = Project(name="p")
        self.db.add_all([user, project])
        await self.db.flush()
        self.room = Room(project_id=project.id, name="r")
        self.me = Agent(name="pm", engine="codex")
        self.dev = Agent(name="dev", engine="codex")
        self.db.add_all([self.room, self.me, self.dev])
        await self.db.flush()
        self.human_p = Participant(room_id=self.room.id, user_id=user.id, role="member")
        self.me_p = Participant(room_id=self.room.id, agent_id=self.me.id, role="member")
        self.dev_p = Participant(room_id=self.room.id, agent_id=self.dev.id, role="member")
        self.db.add_all([self.human_p, self.me_p, self.dev_p])
        await self.db.flush()

    async def say(self, who: Participant, content: str, *, root: Message | None = None) -> Message:
        self.seq += 1
        m = Message(
            room_id=self.room.id, participant_id=who.id, content=content, seq=self.seq,
            parent_message_id=root.id if root else None,
            root_message_id=root.id if root else None,
        )
        self.db.add(m)
        await self.db.flush()
        return m

    async def turn(self, trigger: Message, *, state: str = "leased", **extra) -> AgentTurn:
        self.turns += 1
        t = AgentTurn(
            request_id=f"turn-{self.turns}", room_id=self.room.id,
            target_participant_id=self.me_p.id, agent_id=self.me.id,
            trigger_message_id=trigger.id, thread_root_id=trigger.root_message_id,
            idempotency_key=f"k{self.turns}", state=state, **extra,
        )
        self.db.add(t)
        await self.db.flush()
        return t


@pytest.fixture()
async def room(db) -> _Room:
    r = _Room(db)
    await r.setup()
    return r


def _contents(ctx: dict | None) -> list[str]:
    assert ctx is not None
    return [m["content"] for m in ctx["messages"]]


async def test_mention_to_someone_else_reaches_a_later_turn(db, room) -> None:
    """The defect-7 scenario: pm must see dev's question to qa, not only the answer."""
    first = await room.say(room.human_p, "@pm hi")
    await room.turn(first, state="completed")
    await room.say(room.me_p, "hello")
    await room.say(room.dev_p, "@qa what is done?")
    await room.say(room.human_p, "qa answer")
    trigger = await room.say(room.human_p, "@pm summarize dev and qa")
    turn = await room.turn(trigger)

    ctx = await build_conversation_context(db, turn, trigger)
    # Its own reply is already in the resumed session; the trigger is the turn itself.
    assert _contents(ctx) == ["@qa what is done?", "qa answer"]
    assert ctx["omitted"] == 0
    assert ctx["messages"][0]["speaker"] == "dev"


async def test_first_turn_gets_the_recent_tail_including_its_own_messages(db, room) -> None:
    await room.say(room.me_p, "earlier from me")
    await room.say(room.dev_p, "earlier from dev")
    trigger = await room.say(room.human_p, "@pm ?")
    turn = await room.turn(trigger)

    ctx = await build_conversation_context(db, turn, trigger)
    assert _contents(ctx) == ["earlier from me", "earlier from dev"]
    assert ctx["messages"][0]["speaker"] == "you"


async def test_a_failed_turn_is_not_a_baseline(db, room) -> None:
    first = await room.say(room.human_p, "@pm one")
    await room.turn(first, state="completed")
    between = await room.say(room.dev_p, "between")
    failed_trigger = await room.say(room.human_p, "@pm two")
    await room.turn(failed_trigger, state="failed")
    trigger = await room.say(room.human_p, "@pm three")
    turn = await room.turn(trigger)

    ctx = await build_conversation_context(db, turn, trigger)
    assert between.content in _contents(ctx)
    assert failed_trigger.content in _contents(ctx)


async def test_thread_and_main_channel_are_separate_conversations(db, room) -> None:
    root = await room.say(room.dev_p, "thread root")
    await room.say(room.human_p, "main chatter")
    await room.say(room.human_p, "in thread", root=root)
    trigger = await room.say(room.human_p, "@pm thoughts?", root=root)
    turn = await room.turn(trigger)

    ctx = await build_conversation_context(db, turn, trigger)
    assert _contents(ctx) == ["thread root", "in thread"]

    # A main-channel turn on the root is not a baseline for the thread.
    main_trigger = await room.say(room.human_p, "@pm main")
    await room.turn(main_trigger, state="completed")
    await room.say(room.human_p, "more in thread", root=root)
    trigger2 = await room.say(room.human_p, "@pm again", root=root)
    turn2 = await room.turn(trigger2)
    ctx2 = await build_conversation_context(db, turn2, trigger2)
    assert "thread root" in _contents(ctx2)


async def test_long_gaps_are_capped_and_counted(db, room) -> None:
    root = await room.say(room.dev_p, "thread root")
    for i in range(MAX_CONTEXT_MESSAGES + 5):
        await room.say(room.human_p, f"r{i}", root=root)
    trigger = await room.say(room.human_p, "@pm ?", root=root)
    turn = await room.turn(trigger)

    ctx = await build_conversation_context(db, turn, trigger)
    contents = _contents(ctx)
    # The newest window, with the thread's root kept so the topic is known.
    assert contents[0] == "thread root"
    assert contents[-1] == f"r{MAX_CONTEXT_MESSAGES + 4}"
    assert len(contents) == MAX_CONTEXT_MESSAGES + 1
    assert ctx["omitted"] == 5


async def test_nothing_missed_attaches_nothing(db, room) -> None:
    trigger = await room.say(room.human_p, "@pm first ever")
    turn = await room.turn(trigger)
    assert await build_conversation_context(db, turn, trigger) is None


async def test_context_window_switches_disable_it(db, room) -> None:
    await room.say(room.dev_p, "context")
    trigger = await room.say(room.human_p, "@pm ?")
    turn = await room.turn(trigger)

    room.room.context_window_enabled = False
    assert await build_conversation_context(db, turn, trigger) is None
    room.room.context_window_enabled = True
    room.me.context_window_opt_out = True
    assert await build_conversation_context(db, turn, trigger) is None


async def test_project_execution_turns_keep_their_frozen_input(db, room) -> None:
    await room.say(room.dev_p, "context")
    trigger = await room.say(room.human_p, "@pm ?")
    turn = await room.turn(trigger)
    turn.execution_id = "exec-1"  # not persisted; the builder only reads it
    assert await build_conversation_context(db, turn, trigger) is None

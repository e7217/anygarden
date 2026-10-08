"""Agent-promoted general-room tasks and their thread questions (#806)."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from anygarden.db.models import (
    Agent,
    AgentTurn,
    AgentTurnAttempt,
    Message,
    Participant,
    Room,
    Task,
    User,
)
from anygarden.db.task_input_request_models import TaskInputRequest
from anygarden.general_tasks import (
    GeneralTaskConflict,
    answer_from_thread,
    promote_turn_request,
    request_input,
)
from anygarden.messages.service import append_message
from anygarden.project_executions.service import ExecutionConflict, TurnProof
from anygarden.turns.service import create_turn


async def _seed(db, *, room_name: str = "general") -> dict:
    user = User(email=f"{room_name}@example.com", password_hash="x")
    agent = Agent(name=f"{room_name}-bot", engine="echo", desired_state="running")
    room = Room(name=room_name)
    db.add_all([user, agent, room])
    await db.flush()
    human = Participant(room_id=room.id, user_id=user.id, role="owner")
    bot = Participant(room_id=room.id, agent_id=agent.id, role="member")
    db.add_all([human, bot])
    await db.flush()
    return {"user": user, "agent": agent, "room": room, "human": human, "bot": bot}


async def _leased_turn(db, env: dict, trigger: Message, *, task_id: str | None = None):
    turn = await create_turn(
        db, room_id=env["room"].id, participant_id=env["bot"].id,
        agent_id=env["agent"].id, trigger_message_id=trigger.id,
        thread_root_id=trigger.root_message_id, task_id=task_id,
    )
    attempt = await db.scalar(select(AgentTurnAttempt).where(
        AgentTurnAttempt.turn_id == turn.request_id,
    ))
    attempt.state = "leased"
    attempt.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
    turn.state = "leased"
    await db.flush()
    proof = TurnProof(
        request_id=turn.request_id, attempt=attempt.attempt_number,
        generation=attempt.generation, lease=attempt.lease_token,
    )
    return turn, proof


async def _user_message(db, env: dict, content: str, *, thread_root_id: str | None = None):
    return await append_message(
        db, env["room"].id, env["human"].id, content, {}, thread_root_id=thread_root_id,
    )


async def _promoted(db, content: str = "README 오탈자를 고쳐줘\n자세한 내용") -> tuple:
    env = await _seed(db)
    trigger = await _user_message(db, env, content)
    turn, proof = await _leased_turn(db, env, trigger)
    task, created = await promote_turn_request(db, agent_id=env["agent"].id, proof=proof)
    return env, trigger, turn, proof, task, created


# -- claim_current_request ----------------------------------------------------


@pytest.mark.asyncio
async def test_promotes_the_trigger_message_into_an_in_progress_task(db) -> None:
    env, trigger, turn, _, task, created = await _promoted(db)

    assert created is True
    assert task.source_message_id == trigger.id
    assert task.room_id == env["room"].id
    assert task.status == "in_progress"
    assert task.assignee_participant_id == env["bot"].id
    assert task.started_at is not None
    assert task.title == "README 오탈자를 고쳐줘"
    assert task.spec == trigger.content
    assert turn.task_id == task.id


@pytest.mark.asyncio
async def test_promotion_does_not_wake_the_agent_again(db) -> None:
    env, _, _, _, _, _ = await _promoted(db)

    turns = await db.scalar(select(func.count()).select_from(AgentTurn).where(
        AgentTurn.target_participant_id == env["bot"].id,
    ))
    assignment_notices = (await db.scalars(select(Message).where(
        Message.room_id == env["room"].id,
    ))).all()
    assert turns == 1
    assert not any(
        "task_assignment" in (m.extra_metadata or {}) for m in assignment_notices
    )


@pytest.mark.asyncio
async def test_promoting_twice_in_one_turn_returns_the_same_task(db) -> None:
    env, _, _, proof, task, _ = await _promoted(db)

    again, created = await promote_turn_request(db, agent_id=env["agent"].id, proof=proof)

    assert created is False
    assert again.id == task.id
    assert await db.scalar(select(func.count()).select_from(Task)) == 1


@pytest.mark.asyncio
async def test_long_first_line_is_truncated_for_the_title(db) -> None:
    _, _, _, _, task, _ = await _promoted(db, "가" * 300)

    assert len(task.title) == 120
    assert task.title.endswith("…")


@pytest.mark.asyncio
async def test_stale_lease_is_rejected(db) -> None:
    env = await _seed(db)
    trigger = await _user_message(db, env, "작업해줘")
    _, proof = await _leased_turn(db, env, trigger)
    stale = TurnProof(proof.request_id, proof.attempt, proof.generation, "wrong")

    with pytest.raises(ExecutionConflict):
        await promote_turn_request(db, agent_id=env["agent"].id, proof=stale)


@pytest.mark.asyncio
async def test_missing_proof_is_rejected(db) -> None:
    env = await _seed(db)

    with pytest.raises(GeneralTaskConflict) as exc:
        await promote_turn_request(db, agent_id=env["agent"].id, proof=None)
    assert exc.value.code == "TURN_PROOF_REQUIRED"


@pytest.mark.asyncio
async def test_operating_lead_turn_is_rejected(db) -> None:
    env = await _seed(db)
    env["room"].representative_agent_id = env["agent"].id
    db.add(Room(name="sub", parent_room_id=env["room"].id))
    await db.flush()
    trigger = await _user_message(db, env, "프로젝트 작업")
    _, proof = await _leased_turn(db, env, trigger)

    with pytest.raises(GeneralTaskConflict) as exc:
        await promote_turn_request(db, agent_id=env["agent"].id, proof=proof)
    assert exc.value.code == "CLAIM_OPERATING_ROOM"


@pytest.mark.asyncio
async def test_turn_bound_to_another_task_is_rejected(db) -> None:
    env = await _seed(db)
    trigger = await _user_message(db, env, "작업해줘")
    other = Task(room_id=env["room"].id, title="other", status="in_progress",
                 assignee_participant_id=env["human"].id)
    db.add(other)
    await db.flush()
    _, proof = await _leased_turn(db, env, trigger, task_id=other.id)

    with pytest.raises(GeneralTaskConflict) as exc:
        await promote_turn_request(db, agent_id=env["agent"].id, proof=proof)
    assert exc.value.code == "CLAIM_TURN_HAS_TASK"


@pytest.mark.asyncio
async def test_thread_reply_trigger_is_rejected(db) -> None:
    env = await _seed(db)
    root = await _user_message(db, env, "루트")
    reply = await _user_message(db, env, "스레드 답글", thread_root_id=root.id)
    _, proof = await _leased_turn(db, env, reply)

    with pytest.raises(GeneralTaskConflict) as exc:
        await promote_turn_request(db, agent_id=env["agent"].id, proof=proof)
    assert exc.value.code == "CLAIM_SOURCE_INVALID"


@pytest.mark.asyncio
async def test_system_notice_trigger_is_rejected(db) -> None:
    env = await _seed(db)
    notice = await append_message(
        db, env["room"].id, env["human"].id, "알림", {"system_origin": "x"},
    )
    _, proof = await _leased_turn(db, env, notice)

    with pytest.raises(GeneralTaskConflict) as exc:
        await promote_turn_request(db, agent_id=env["agent"].id, proof=proof)
    assert exc.value.code == "CLAIM_SOURCE_INVALID"


@pytest.mark.asyncio
async def test_agent_message_trigger_is_rejected(db) -> None:
    env = await _seed(db)
    peer = Agent(name="peer", engine="echo")
    db.add(peer)
    await db.flush()
    peer_part = Participant(room_id=env["room"].id, agent_id=peer.id, role="member")
    db.add(peer_part)
    await db.flush()
    from_peer = await append_message(db, env["room"].id, peer_part.id, "부탁해", {})
    _, proof = await _leased_turn(db, env, from_peer)

    with pytest.raises(GeneralTaskConflict) as exc:
        await promote_turn_request(db, agent_id=env["agent"].id, proof=proof)
    assert exc.value.code == "CLAIM_SOURCE_INVALID"


@pytest.mark.asyncio
async def test_message_already_linked_to_someone_elses_task_is_rejected(db) -> None:
    env = await _seed(db)
    trigger = await _user_message(db, env, "작업해줘")
    linked = Task(room_id=env["room"].id, title="mine", status="todo",
                  source_message_id=trigger.id)
    db.add(linked)
    await db.flush()
    _, proof = await _leased_turn(db, env, trigger)

    with pytest.raises(GeneralTaskConflict) as exc:
        await promote_turn_request(db, agent_id=env["agent"].id, proof=proof)
    assert exc.value.code == "TASK_SOURCE_ALREADY_LINKED"
    assert exc.value.extra["existing_task_id"] == linked.id


# -- request_task_input / thread answer ----------------------------------------


async def _asked(db) -> tuple:
    env, trigger, turn, proof, task, _ = await _promoted(db)
    request, messages = await request_input(
        db, agent_id=env["agent"].id, proof=proof,
        question_key="deadline", question="마감일이 언제인가요?",
    )
    return env, trigger, turn, proof, task, request, messages


@pytest.mark.asyncio
async def test_question_blocks_the_task_and_is_posted_in_its_thread(db) -> None:
    _, trigger, turn, _, task, request, messages = await _asked(db)

    await db.refresh(task)
    assert task.status == "blocked"
    assert request.status == "pending"
    assert request.turn_request_id == turn.request_id
    [question] = messages
    assert question.root_message_id == trigger.id
    assert question.participant_id is None
    assert "마감일이 언제인가요?" in question.content
    assert question.extra_metadata["system_origin"] == "task_input_request"
    assert request.question_message_id == question.id


@pytest.mark.asyncio
async def test_question_requires_a_promoted_task(db) -> None:
    env = await _seed(db)
    trigger = await _user_message(db, env, "질문만")
    _, proof = await _leased_turn(db, env, trigger)

    with pytest.raises(GeneralTaskConflict) as exc:
        await request_input(db, agent_id=env["agent"].id, proof=proof,
                            question_key="k", question="q")
    assert exc.value.code == "REQUEST_TASK_NOT_CURRENT"


@pytest.mark.asyncio
async def test_same_question_key_is_idempotent_and_different_text_is_rejected(db) -> None:
    env, _, _, proof, _, request, _ = await _asked(db)

    again, messages = await request_input(
        db, agent_id=env["agent"].id, proof=proof,
        question_key="deadline", question="마감일이 언제인가요?",
    )
    assert again.id == request.id
    assert messages == []
    with pytest.raises(GeneralTaskConflict) as exc:
        await request_input(db, agent_id=env["agent"].id, proof=proof,
                            question_key="deadline", question="다른 질문")
    assert exc.value.code == "REQUEST_KEY_REUSED"


@pytest.mark.asyncio
async def test_only_one_question_waits_at_a_time(db) -> None:
    env, _, _, proof, _, _, _ = await _asked(db)

    with pytest.raises(GeneralTaskConflict) as exc:
        await request_input(db, agent_id=env["agent"].id, proof=proof,
                            question_key="budget", question="예산은요?")
    assert exc.value.code == "REQUEST_PENDING_EXISTS"


@pytest.mark.asyncio
async def test_thread_answer_resumes_the_same_task_with_a_new_turn(db) -> None:
    env, trigger, turn, _, task, request, _ = await _asked(db)
    reply = await _user_message(db, env, "다음 주 금요일", thread_root_id=trigger.id)

    result = await answer_from_thread(db, reply=reply, user_id=env["user"].id)

    assert result is not None
    assert result.requester_participant_id == env["bot"].id
    await db.refresh(task)
    await db.refresh(request)
    assert request.status == "answered"
    assert request.answer == "다음 주 금요일"
    assert request.answer_message_id == reply.id
    assert task.status == "in_progress"
    assert task.assignee_participant_id == env["bot"].id
    assert "다음 주 금요일" in task.spec and "마감일이 언제인가요?" in task.spec
    [resume] = result.messages
    assert request.resume_message_id == resume.id
    resumed = await db.scalar(select(AgentTurn).where(
        AgentTurn.trigger_message_id == resume.id,
    ))
    assert resumed is not None
    assert resumed.task_id == task.id
    assert resumed.request_id != turn.request_id


@pytest.mark.asyncio
async def test_resumed_turn_can_claim_again_idempotently(db) -> None:
    env, trigger, _, _, task, _, _ = await _asked(db)
    reply = await _user_message(db, env, "금요일", thread_root_id=trigger.id)
    result = await answer_from_thread(db, reply=reply, user_id=env["user"].id)
    resume_turn = await db.scalar(select(AgentTurn).where(
        AgentTurn.trigger_message_id == result.messages[0].id,
    ))
    attempt = await db.scalar(select(AgentTurnAttempt).where(
        AgentTurnAttempt.turn_id == resume_turn.request_id,
    ))
    attempt.state = "leased"
    attempt.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
    await db.flush()
    proof = TurnProof(resume_turn.request_id, attempt.attempt_number,
                      attempt.generation, attempt.lease_token)

    again, created = await promote_turn_request(db, agent_id=env["agent"].id, proof=proof)

    assert created is False
    assert again.id == task.id


@pytest.mark.asyncio
async def test_second_reply_is_ordinary_conversation(db) -> None:
    env, trigger, _, _, _, _, _ = await _asked(db)
    first = await _user_message(db, env, "금요일", thread_root_id=trigger.id)
    assert await answer_from_thread(db, reply=first, user_id=env["user"].id) is not None
    second = await _user_message(db, env, "고마워", thread_root_id=trigger.id)

    assert await answer_from_thread(db, reply=second, user_id=env["user"].id) is None


@pytest.mark.asyncio
async def test_reply_in_a_thread_without_a_question_is_ignored(db) -> None:
    env, trigger, _, _, _, _ = await _promoted(db)
    reply = await _user_message(db, env, "그냥 대화", thread_root_id=trigger.id)

    assert await answer_from_thread(db, reply=reply, user_id=env["user"].id) is None


@pytest.mark.asyncio
async def test_old_turn_cannot_ask_again_after_the_answer(db) -> None:
    """QA-06 F1: the answer can arrive before the asking turn ends."""
    env, trigger, _, proof, _, _, _ = await _asked(db)
    reply = await _user_message(db, env, "금요일", thread_root_id=trigger.id)
    await answer_from_thread(db, reply=reply, user_id=env["user"].id)

    with pytest.raises(GeneralTaskConflict) as exc:
        await request_input(db, agent_id=env["agent"].id, proof=proof,
                            question_key="budget", question="예산은요?")
    assert exc.value.code == "TASK_TURN_SUPERSEDED"


@pytest.mark.asyncio
async def test_question_rows_are_unique_per_task_and_key(db) -> None:
    _, _, _, _, task, _, _ = await _asked(db)

    rows = await db.scalar(select(func.count()).select_from(TaskInputRequest).where(
        TaskInputRequest.task_id == task.id,
    ))
    assert rows == 1


# -- mark_task_status on a superseded general turn -----------------------------


@pytest.mark.asyncio
async def test_old_turn_cannot_change_the_task_after_the_answer(db) -> None:
    from anygarden.mcp.tools import mark_task_status

    env, trigger, _, proof, task, _, _ = await _asked(db)
    reply = await _user_message(db, env, "금요일", thread_root_id=trigger.id)
    await answer_from_thread(db, reply=reply, user_id=env["user"].id)

    result = await mark_task_status(
        db, agent_id=env["agent"].id,
        arguments={"task_id": task.id, "status": "done"}, proof=proof,
    )

    assert result["isError"] is True
    assert "TASK_TURN_SUPERSEDED" in result["content"][0]["text"]
    await db.refresh(task)
    assert task.status == "in_progress"


@pytest.mark.asyncio
async def test_current_turn_finishes_its_promoted_task(db) -> None:
    from anygarden.mcp.tools import mark_task_status

    env, _, _, proof, task, _ = await _promoted(db)

    result = await mark_task_status(
        db, agent_id=env["agent"].id,
        arguments={"task_id": task.id, "status": "done", "result_markdown": "고침"},
        proof=proof,
    )

    assert result["isError"] is False
    await db.refresh(task)
    assert task.status == "done"
    assert task.result_markdown == "고침"


# -- MCP round trip ------------------------------------------------------------


@pytest_asyncio.fixture()
async def rpc_env():
    from anygarden.app import create_app
    from anygarden.auth.token import generate_token, hash_agent_token
    from anygarden.config import AnygardenSettings
    from anygarden.db.engine import build_engine, build_session_factory
    from anygarden.db.models import AgentToken, Base

    config = AnygardenSettings(
        db_url="sqlite+aiosqlite://", jwt_secret=secrets.token_urlsafe(32),
    )
    engine = build_engine(config.db_url)
    factory = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = factory
    async with factory() as db:
        env = await _seed(db)
        plain = generate_token()
        token_hash, hint = hash_agent_token(plain)
        db.add(AgentToken(agent_id=env["agent"].id, token_hash=token_hash, lookup_hint=hint))
        trigger = await _user_message(db, env, "로그를 조사해줘")
        _, proof = await _leased_turn(db, env, trigger)
        await db.commit()
        ids = {"agent_id": env["agent"].id, "trigger_id": trigger.id, "room_id": env["room"].id}
    headers = {
        "Authorization": f"Bearer {plain}",
        "X-Anygarden-Turn-Request-Id": proof.request_id,
        "X-Anygarden-Turn-Attempt": str(proof.attempt),
        "X-Anygarden-Turn-Generation": str(proof.generation),
        "X-Anygarden-Turn-Lease": proof.lease,
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield {"client": client, "headers": headers, "factory": factory, **ids}
    await engine.dispose()


async def _rpc(env: dict, name: str, arguments: dict | None = None, *, headers=None) -> dict:
    resp = await env["client"].post(
        "/mcp/rpc",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
              "params": {"name": name, "arguments": arguments or {}}},
        headers=headers or env["headers"],
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["result"]


@pytest.mark.asyncio
async def test_tools_are_listed(rpc_env) -> None:
    resp = await rpc_env["client"].post(
        "/mcp/rpc", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers=rpc_env["headers"],
    )
    names = {tool["name"] for tool in resp.json()["result"]["tools"]}
    assert {"claim_current_request", "request_task_input"} <= names


@pytest.mark.asyncio
async def test_rpc_claim_then_ask_persists(rpc_env) -> None:
    claimed = await _rpc(rpc_env, "claim_current_request")
    assert claimed["isError"] is False, claimed
    task_id = claimed["structuredContent"]["task_id"]

    asked = await _rpc(rpc_env, "request_task_input",
                       {"question_key": "range", "question": "어느 기간의 로그인가요?"})
    assert asked["isError"] is False, asked

    async with rpc_env["factory"]() as db:
        task = await db.get(Task, task_id)
        assert task.source_message_id == rpc_env["trigger_id"]
        assert task.status == "blocked"
        question = await db.scalar(select(Message).where(
            Message.root_message_id == rpc_env["trigger_id"],
        ))
        assert "어느 기간의 로그인가요?" in question.content


@pytest.mark.asyncio
async def test_rpc_without_turn_proof_is_an_error(rpc_env) -> None:
    result = await _rpc(rpc_env, "claim_current_request",
                        headers={"Authorization": rpc_env["headers"]["Authorization"]})

    assert result["isError"] is True
    async with rpc_env["factory"]() as db:
        assert await db.scalar(select(func.count()).select_from(Task)) == 0

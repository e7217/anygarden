"""Collaboration health metrics: delivery, stuck work, completion and cost."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from anygarden.db.models import AgentTurn, Message, Project, Room, Task, UsageLedger
from httpx import ASGITransport, AsyncClient

from .test_agents_api import agents_env as agents_env


def _turn(room_id: str, state: str, **kw) -> AgentTurn:
    return AgentTurn(
        request_id=str(uuid4()),
        idempotency_key=str(uuid4()),
        room_id=room_id,
        state=state,
        **kw,
    )


async def _seed(factory):
    now = datetime.now(UTC)
    async with factory() as db:
        project, other = Project(name="bench"), Project(name="other")
        db.add_all([project, other])
        await db.flush()
        room, sub, elsewhere = (
            Room(project_id=project.id, name="ops"),
            Room(project_id=project.id, name="sub"),
            Room(project_id=other.id, name="x"),
        )
        db.add_all([room, sub, elsewhere])
        await db.flush()
        replies = [Message(room_id=r.id, content="ok", seq=i) for i, r in enumerate((room, sub), 1)]
        db.add_all(replies)
        await db.flush()
        stale = now - timedelta(hours=1)
        db.add_all(
            [
                _turn(room.id, "completed", accepted_message_id=replies[0].id),
                _turn(sub.id, "completed", accepted_message_id=replies[1].id, retry_count=1),
                _turn(room.id, "completed", terminal_reason="agent_skipped"),
                _turn(room.id, "completed"),  # finished, said nothing: silent loss
                _turn(room.id, "failed"),
                _turn(sub.id, "leased", updated_at=stale),  # stuck
                _turn(sub.id, "pending"),  # open but fresh
                _turn(elsewhere.id, "failed"),
                Task(room_id=room.id, title="a", status="done"),
                Task(room_id=sub.id, title="b", status="done"),
                Task(room_id=sub.id, title="c", status="failed"),
                Task(room_id=room.id, title="d", status="blocked"),
                Task(room_id=elsewhere.id, title="e", status="failed"),
                UsageLedger(
                    room_id=room.id, identity_kind="agent", identity_id="a",
                    model_name="m", prompt_tokens=100, completion_tokens=40,
                    cost_usd=0.5, status_code=200,
                ),
                UsageLedger(
                    room_id=sub.id, identity_kind="agent", identity_id="b",
                    model_name="m", prompt_tokens=10, completion_tokens=5,
                    status_code=200,
                ),
                UsageLedger(
                    room_id=elsewhere.id, identity_kind="agent", identity_id="c",
                    model_name="m", cost_usd=9.0, status_code=200,
                ),
            ]
        )
        await db.commit()
        return project.id


async def test_project_scoped_metrics(agents_env) -> None:
    project_id = await _seed(agents_env["factory"])

    transport = ASGITransport(app=agents_env["app"])
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/metrics/collaboration",
            params={"project_id": project_id},
            headers={"Authorization": f"Bearer {agents_env['token']}"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["room_count"] == 2
    turns = body["turns"]
    assert turns["total"] == 7
    assert turns["replied"] == 2
    assert turns["skipped"] == 1
    assert turns["completed_without_reply"] == 1
    assert turns["retried"] == 1
    assert turns["stuck"] == 1
    assert turns["failure_rate"] == round(1 / 5, 4)
    assert turns["silent_loss_rate"] == round(1 / 3, 4)
    tasks = body["tasks"]
    assert tasks["by_status"] == {"done": 2, "failed": 1, "blocked": 1}
    assert tasks["completion_rate"] == round(2 / 3, 4)
    cost = body["cost"]
    assert cost["requests"] == 2
    assert cost["requests_without_cost"] == 1
    assert cost["prompt_tokens"] == 110
    assert cost["cost_usd"] == 0.5
    assert cost["cost_per_done_task"] == 0.25


async def test_unscoped_metrics_cover_all_rooms(agents_env) -> None:
    await _seed(agents_env["factory"])

    transport = ASGITransport(app=agents_env["app"])
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/metrics/collaboration",
            params={"since": (datetime.now(UTC) - timedelta(minutes=5)).isoformat()},
            headers={"Authorization": f"Bearer {agents_env['token']}"},
        )

    body = resp.json()
    assert body["room_count"] is None
    assert body["turns"]["total"] == 8
    assert body["cost"]["cost_usd"] == 9.5


async def test_metrics_are_admin_only(agents_env) -> None:
    transport = ASGITransport(app=agents_env["app"])
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/metrics/collaboration",
            headers={"Authorization": f"Bearer {agents_env['regular_token']}"},
        )

    assert resp.status_code == 403

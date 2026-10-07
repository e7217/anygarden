# ruff: noqa: F811
"""#739 — who a human message wakes in each room kind.

A root message wakes only the agents it
mentions: no mention → no turn, ``@everyone`` → every agent, a single
mention → that agent, and a room with exactly one agent → that agent even
without a mention. The server records these calls as ordinary ``user``
mentions (``via`` marks the expanded ones) so agents and turn creation use
the explicit-mention path.
"""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from starlette.testclient import TestClient

from anygarden.db.models import Agent, AgentTurn, Participant, Room

# ws_env fixture lives in test_ws_handler.py; importing it registers it.
from tests.test_ws_handler import ws_env  # noqa: F401


async def _seed_agents(sf, room_id: str, names: list[str]) -> list[str]:
    """Add running agents to the room; return their pids."""
    pids: list[str] = []
    async with sf() as db:
        for name in names:
            agent = Agent(name=name, engine="codex", actual_state="running")
            db.add(agent)
            await db.flush()
            part = Participant(room_id=room_id, agent_id=agent.id, role="member")
            db.add(part)
            await db.flush()
            pids.append(part.id)
        await db.commit()
    return pids


def _user_send(app, room_id: str, token: str, content: str, thread_root_id=None) -> dict:
    frame = {"type": "send", "content": content}
    if thread_root_id is not None:
        frame["thread_root_id"] = thread_root_id
    with TestClient(app) as client, client.websocket_connect(
        f"/ws/rooms/{room_id}", subprotocols=["anygarden.v1", f"bearer.{token}"],
    ) as ws:
        ws.receive_text()  # welcome
        ws.send_text(json.dumps(frame))
        # Other frames (e.g. the roster broadcast when a room query
        # auto-joins the representative) may arrive before the echo.
        for _ in range(10):
            out = json.loads(ws.receive_text())
            if out["type"] == "message":
                return out
    raise AssertionError("message echo never arrived")  # pragma: no cover


async def _turn_targets(sf, trigger_message_id: str) -> set[str]:
    async with sf() as db:
        turns = (
            await db.scalars(
                select(AgentTurn).where(
                    AgentTurn.trigger_message_id == trigger_message_id
                )
            )
        ).all()
    return {t.target_participant_id for t in turns}


class TestMentionedOnlyRootMessage:
    @pytest.mark.asyncio
    async def test_unmentioned_message_wakes_nobody(self, ws_env) -> None:
        env = ws_env
        await _seed_agents(env["session_factory"], env["room"].id, ["A", "B"])

        out = _user_send(env["app"], env["room"].id, env["token"], "누가 좀 봐줘")

        assert "mentions" not in (out.get("metadata") or {})
        assert await _turn_targets(env["session_factory"], out["id"]) == set()

    @pytest.mark.asyncio
    async def test_everyone_wakes_every_agent(self, ws_env) -> None:
        env = ws_env
        pids = await _seed_agents(
            env["session_factory"], env["room"].id, ["A", "B", "C"]
        )

        out = _user_send(env["app"], env["room"].id, env["token"], "@everyone 소개해 줘")

        mentions = out["metadata"]["mentions"]
        assert {m["id"] for m in mentions} == set(pids)
        assert all(m == {"type": "user", "id": m["id"], "via": "everyone"} for m in mentions)
        assert out["content"] == "@everyone 소개해 줘"
        assert await _turn_targets(env["session_factory"], out["id"]) == set(pids)

    @pytest.mark.asyncio
    async def test_single_mention_wakes_only_that_agent(self, ws_env) -> None:
        env = ws_env
        pa, _pb = await _seed_agents(
            env["session_factory"], env["room"].id, ["A", "B"]
        )

        out = _user_send(env["app"], env["room"].id, env["token"], f"<@user:{pa}> 부탁해")

        assert out["metadata"]["mentions"] == [{"type": "user", "id": pa}]
        assert await _turn_targets(env["session_factory"], out["id"]) == {pa}

    @pytest.mark.asyncio
    async def test_legacy_name_mention_wakes_that_agent(self, ws_env) -> None:
        env = ws_env
        pa, _pb = await _seed_agents(
            env["session_factory"], env["room"].id, ["alpha", "beta"]
        )

        out = _user_send(env["app"], env["room"].id, env["token"], "@Alpha 부탁해")

        assert out["metadata"]["mentions"] == [{"type": "legacy", "name": "Alpha"}]
        assert await _turn_targets(env["session_factory"], out["id"]) == {pa}

    @pytest.mark.asyncio
    async def test_one_agent_room_needs_no_mention(self, ws_env) -> None:
        env = ws_env
        (pa,) = await _seed_agents(
            env["session_factory"], env["room"].id, ["solo"]
        )

        out = _user_send(env["app"], env["room"].id, env["token"], "안녕")

        assert out["metadata"]["mentions"] == [
            {"type": "user", "id": pa, "via": "sole_agent"}
        ]
        assert await _turn_targets(env["session_factory"], out["id"]) == {pa}

    @pytest.mark.asyncio
    async def test_one_agent_room_mentioning_a_human_does_not_wake_agent(
        self, ws_env
    ) -> None:
        env = ws_env
        await _seed_agents(env["session_factory"], env["room"].id, ["solo"])
        human_pid = env["participant"].id

        out = _user_send(
            env["app"], env["room"].id, env["token"], f"<@user:{human_pid}> 메모"
        )

        assert out["metadata"]["mentions"] == [{"type": "user", "id": human_pid}]
        assert await _turn_targets(env["session_factory"], out["id"]) == set()

    @pytest.mark.asyncio
    async def test_everyone_in_thread_reply_wakes_every_agent(self, ws_env) -> None:
        env = ws_env
        pids = await _seed_agents(
            env["session_factory"], env["room"].id, ["A", "B"]
        )
        root = _user_send(env["app"], env["room"].id, env["token"], "스레드 시작")

        reply = _user_send(
            env["app"], env["room"].id, env["token"], "@everyone 의견?",
            thread_root_id=root["id"],
        )

        assert {m["id"] for m in reply["metadata"]["mentions"]} == set(pids)
        assert await _turn_targets(env["session_factory"], reply["id"]) == set(pids)


class TestRestPathExpansion:
    @pytest.mark.asyncio
    async def test_rest_send_expands_everyone(self, ws_env) -> None:
        env = ws_env
        pids = await _seed_agents(
            env["session_factory"], env["room"].id, ["A", "B"]
        )
        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/rooms/{env['room'].id}/messages",
                json={"content": "@everyone 공지"},
                headers={"Authorization": f"Bearer {env['token']}"},
            )
        assert resp.status_code in (200, 201), resp.text
        mentions = resp.json()["metadata"]["mentions"]
        assert {m["id"] for m in mentions} == set(pids)
        assert {m["via"] for m in mentions} == {"everyone"}

    @pytest.mark.asyncio
    async def test_rest_send_in_one_agent_room_calls_that_agent(self, ws_env) -> None:
        env = ws_env
        (pa,) = await _seed_agents(
            env["session_factory"], env["room"].id, ["solo"]
        )
        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/rooms/{env['room'].id}/messages",
                json={"content": "안녕"},
                headers={"Authorization": f"Bearer {env['token']}"},
            )
        assert resp.status_code in (200, 201), resp.text
        assert resp.json()["metadata"]["mentions"] == [
            {"type": "user", "id": pa, "via": "sole_agent"}
        ]


class TestRoomQueryRepresentative:
    @pytest.mark.asyncio
    async def test_room_query_wakes_only_the_representative(self, ws_env) -> None:
        """A ``#room`` query is forwarded by the target room's representative,
        so that agent still gets a turn."""
        env = ws_env
        sf = env["session_factory"]
        await _seed_agents(sf, env["room"].id, ["A", "B"])
        async with sf() as db:
            rep = Agent(name="rep", engine="codex", actual_state="running")
            db.add(rep)
            await db.flush()
            target = Room(
                project_id=env["room"].project_id,
                name="target",
                representative_agent_id=rep.id,
            )
            db.add(target)
            await db.commit()
            rep_id, target_id = rep.id, target.id

        out = _user_send(env["app"], env["room"].id, env["token"], f"<#room:{target_id}> 의견?")

        assert out["metadata"]["room_query"]["representative_agent_id"] == rep_id
        async with sf() as db:
            turns = (
                await db.scalars(
                    select(AgentTurn).where(AgentTurn.trigger_message_id == out["id"])
                )
            ).all()
        assert [t.agent_id for t in turns] == [rep_id]

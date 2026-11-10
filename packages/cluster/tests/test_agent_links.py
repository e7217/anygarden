"""Agent links and question chains (#1006).

An agent may ask any agent it is linked to — from any room. A linked agent
outside the room is seated for the question's thread only, and a peer that
was asked may ask on: the question waits for the peer's own questions and
settles with the peer's final answer. A chain never asks an agent that is
already answering higher up in it, and a person can stop a whole chain.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from anygarden.db.models import (
    Agent,
    AgentLink,
    AgentTurn,
    Message,
    Participant,
    PeerAskGroup,
    PeerAskTarget,
)
from anygarden.orchestration.peer_fanin import dispatch_peer_ask_groups
from tests.test_mcp_ask_peer import (
    _agent_send,
    _ask_peer_raw,
    _open_turn,
    _proof,
    _seed_sender_and_peer,
    _user_send,
)
from tests.test_peer_mention_safety_net import _add_token
from tests.test_ws_handler import ws_env as ws_env


@pytest.fixture(autouse=True)
def _no_recovery_loop(monkeypatch) -> None:
    monkeypatch.setenv("ANYGARDEN_TURN_RECOVERY_INTERVAL_SEC", "0")
    monkeypatch.delenv("ANYGARDEN_ASK_PEER_MAX_DEPTH", raising=False)
    monkeypatch.delenv("ANYGARDEN_ASK_PEER_MAX_CALLS", raising=False)


async def _add_outside_agent(sf, name: str) -> str:
    """An agent that can run but is not in any room; returns its agent id."""
    async with sf() as db:
        agent = Agent(name=name, engine="codex", actual_state="running", desired_state="running")
        db.add(agent)
        await db.commit()
        return agent.id


async def _agent_of(sf, pid: str) -> str:
    async with sf() as db:
        return (await db.get(Participant, pid)).agent_id


async def _link(sf, from_agent: str, to_agent: str) -> None:
    async with sf() as db:
        db.add(AgentLink(from_agent_id=from_agent, to_agent_id=to_agent))
        await db.commit()


async def _participant_for(sf, room_id: str, agent_id: str) -> Participant | None:
    async with sf() as db:
        return (
            await db.scalars(
                select(Participant).where(
                    Participant.room_id == room_id, Participant.agent_id == agent_id
                )
            )
        ).one_or_none()


async def _target_for(sf, pid: str) -> PeerAskTarget:
    async with sf() as db:
        return (
            await db.scalars(
                select(PeerAskTarget).where(PeerAskTarget.target_participant_id == pid)
            )
        ).one()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestLinkApi:
    @pytest.mark.asyncio
    @pytest.mark.req("COL-14")
    async def test_create_list_delete_without_conditions(self, ws_env) -> None:
        app, sf = ws_env["app"], ws_env["session_factory"]
        a = await _add_outside_agent(sf, "기획자")
        b = await _add_outside_agent(sf, "리뷰어")

        with TestClient(app) as client:
            created = client.post(
                "/api/v1/agent-links",
                headers=_auth(ws_env["token"]),
                json={"from_agent_id": a, "to_agent_id": b},
            )
            assert created.status_code == 201, created.text
            link = created.json()
            assert "when_to_ask" not in link

            listed = client.get("/api/v1/agent-links", headers=_auth(ws_env["token"])).json()
            assert [ln["id"] for ln in listed["links"]] == [link["id"]]
            assert {ag["name"] for ag in listed["agents"]} >= {"기획자", "리뷰어"}

            patched = client.patch(
                f"/api/v1/agent-links/{link['id']}",
                headers=_auth(ws_env["token"]),
                json={"when_to_ask": "PR을 올리기 전에"},
            )
            assert patched.status_code == 405

            assert client.delete(
                f"/api/v1/agent-links/{link['id']}", headers=_auth(ws_env["token"])
            ).status_code == 204
            assert client.get(
                "/api/v1/agent-links", headers=_auth(ws_env["token"])
            ).json()["links"] == []

    @pytest.mark.asyncio
    async def test_invalid_links_are_refused(self, ws_env) -> None:
        app, sf = ws_env["app"], ws_env["session_factory"]
        a = await _add_outside_agent(sf, "a")
        b = await _add_outside_agent(sf, "b")

        def post(body: dict) -> int:
            return client.post(
                "/api/v1/agent-links", headers=_auth(ws_env["token"]), json=body
            ).status_code

        with TestClient(app) as client:
            assert post({"from_agent_id": a, "to_agent_id": a}) == 400
            assert post({"from_agent_id": a, "to_agent_id": "nope"}) == 404
            assert post({"from_agent_id": a, "to_agent_id": b, "when_to_ask": "  "}) == 422
            assert post({"from_agent_id": a, "to_agent_id": b, "when_to_ask": "x" * 301}) == 422
            assert post({"from_agent_id": a, "to_agent_id": b}) == 201
            assert post({"from_agent_id": a, "to_agent_id": b}) == 409

    @pytest.mark.asyncio
    async def test_agents_cannot_edit_links(self, ws_env) -> None:
        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        agent_token, sender_pid, peer_pid = await _seed_sender_and_peer(sf, room)

        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/agent-links",
                headers=_auth(agent_token),
                json={
                    "from_agent_id": await _agent_of(sf, sender_pid),
                    "to_agent_id": await _agent_of(sf, peer_pid),
                },
            )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_welcome_carries_each_agents_links(self, ws_env) -> None:
        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        _token, sender_pid, _peer_pid = await _seed_sender_and_peer(sf, room)
        sender = await _agent_of(sf, sender_pid)
        outside = await _add_outside_agent(sf, "보안 담당")
        await _link(sf, sender, outside)

        with TestClient(app) as client, client.websocket_connect(
            f"/ws/rooms/{room.id}",
            subprotocols=["anygarden.v1", f"bearer.{ws_env['token']}"],
        ) as ws:
            welcome = json.loads(ws.receive_text())

        assert welcome["agent_links"][sender] == [{
            "agent_id": outside,
            "display_name": "보안 담당",
            "description": None,
        }]


class TestAskThroughALink:
    @pytest.mark.asyncio
    @pytest.mark.req("COL-15")
    async def test_linked_agent_outside_the_room_is_seated_for_the_thread(
        self, ws_env
    ) -> None:
        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        sender_token, sender_pid, _peer_pid = await _seed_sender_and_peer(sf, room)
        outside = await _add_outside_agent(sf, "보안 담당")
        await _link(sf, await _agent_of(sf, sender_pid), outside)

        with TestClient(app) as client:
            trigger = _user_send(client, room.id, ws_env["token"], f"<@user:{sender_pid}> 점검")
            result = _ask_peer_raw(client, sender_token, room.id, [
                {"agent_id": outside, "question": "토큰 검증 봐 줘"},
            ])

        assert result["isError"] is False, result
        assert result["structuredContent"]["status"] == "sent"
        seated = await _participant_for(sf, room.id, outside)
        assert seated is not None and seated.joined_via == "link"
        row = await _target_for(sf, seated.id)
        async with sf() as db:
            question = await db.get(Message, row.question_message_id)
            turn = await db.get(AgentTurn, row.request_id)
        assert question.root_message_id == trigger["id"]
        assert question.extra_metadata["peer_ask"]["depth"] == 1
        assert turn.state == "pending" and turn.target_participant_id == seated.id

    @pytest.mark.asyncio
    async def test_unlinked_agent_outside_the_room_is_a_tool_error(self, ws_env) -> None:
        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        sender_token, sender_pid, _peer_pid = await _seed_sender_and_peer(sf, room)
        outside = await _add_outside_agent(sf, "남")

        with TestClient(app) as client:
            _user_send(client, room.id, ws_env["token"], f"<@user:{sender_pid}> 점검")
            result = _ask_peer_raw(client, sender_token, room.id, [
                {"agent_id": outside, "question": "q"},
            ])

        assert result["isError"] is True
        assert "no link" in result["content"][0]["text"]
        assert await _participant_for(sf, room.id, outside) is None

    @pytest.mark.asyncio
    async def test_a_room_member_can_be_named_by_agent_id_without_a_link(
        self, ws_env
    ) -> None:
        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        sender_token, sender_pid, peer_pid = await _seed_sender_and_peer(sf, room)

        with TestClient(app) as client:
            _user_send(client, room.id, ws_env["token"], f"<@user:{sender_pid}> 점검")
            result = _ask_peer_raw(client, sender_token, room.id, [
                {"agent_id": await _agent_of(sf, peer_pid), "question": "q"},
            ])

        assert result["structuredContent"]["status"] == "sent"
        assert (await _target_for(sf, peer_pid)).state == "pending"

    @pytest.mark.asyncio
    async def test_thread_only_agent_is_not_a_room_member(self, ws_env) -> None:
        """No @everyone, no one-agent rule, no member list for it."""
        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        sender_token, sender_pid, peer_pid = await _seed_sender_and_peer(sf, room)
        outside = await _add_outside_agent(sf, "보안 담당")
        await _link(sf, await _agent_of(sf, sender_pid), outside)

        with TestClient(app) as client:
            _user_send(client, room.id, ws_env["token"], f"<@user:{sender_pid}> 점검")
            _ask_peer_raw(client, sender_token, room.id, [{"agent_id": outside, "question": "q"}])
            everyone = _user_send(client, room.id, ws_env["token"], "@everyone 안녕")
            detail = client.get(f"/api/v1/rooms/{room.id}", headers=_auth(ws_env["token"])).json()

        seated = await _participant_for(sf, room.id, outside)
        async with sf() as db:
            woken = set(
                (
                    await db.scalars(
                        select(AgentTurn.target_participant_id).where(
                            AgentTurn.trigger_message_id == everyone["id"]
                        )
                    )
                ).all()
            )
        assert seated.id not in woken
        assert {sender_pid, peer_pid} >= woken
        by_id = {p["id"]: p for p in detail["participants"]}
        assert by_id[seated.id]["joined_via"] == "link"
        assert by_id[sender_pid]["joined_via"] is None


async def _nested_setup(ws_env):
    """A (asked by the user) → B (room member) → C (linked, outside)."""
    sf, room = ws_env["session_factory"], ws_env["room"]
    a_token, a_pid, b_pid = await _seed_sender_and_peer(sf, room)
    b_token = await _add_token(sf, b_pid)
    c = await _add_outside_agent(sf, "감사 담당")
    await _link(sf, await _agent_of(sf, b_pid), c)
    return a_token, a_pid, b_token, b_pid, c


class TestNestedChain:
    @pytest.mark.asyncio
    @pytest.mark.req("COL-16")
    async def test_a_peer_asks_on_and_answers_after_its_own_peer(self, ws_env) -> None:
        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        a_token, a_pid, b_token, b_pid, c = await _nested_setup(ws_env)

        with TestClient(app) as client:
            trigger = _user_send(client, room.id, ws_env["token"], f"<@user:{a_pid}> 점검")
            _ask_peer_raw(client, a_token, room.id, [{"participant_id": b_pid, "question": "검토"}])
            _agent_send(client, room.id, a_token, "A 초안", await _proof(sf, a_pid))

            asked = _ask_peer_raw(client, b_token, room.id, [{"agent_id": c, "question": "감사 로그?"}])
            assert asked["structuredContent"]["status"] == "sent", asked
            _agent_send(client, room.id, b_token, "B 초안", await _proof(sf, b_pid),
                        thread_root_id=trigger["id"])

            b_row = await _target_for(sf, b_pid)
            async with sf() as db:
                b_group = (await db.scalars(
                    select(PeerAskGroup).where(PeerAskGroup.caller_participant_id == b_pid)
                )).one()
            assert b_row.state == "pending" and b_row.awaiting_group_id == b_group.id
            assert b_group.depth == 2 and b_group.parent_target_id == b_row.id
            assert await dispatch_peer_ask_groups(sf, None) == 0

            c_pid = (await _participant_for(sf, room.id, c)).id
            c_token = await _add_token(sf, c_pid)
            _agent_send(client, room.id, c_token, "남지 않음", await _proof(sf, c_pid),
                        thread_root_id=trigger["id"])
            # C answered: B is woken; B's question now follows B's wake turn.
            assert await dispatch_peer_ask_groups(sf, None) == 1
            b_wake = await _open_turn(sf, b_pid)
            b_row = await _target_for(sf, b_pid)
            assert b_row.awaiting_group_id is None and b_row.request_id == b_wake.request_id

            _agent_send(client, room.id, b_token, "B 최종: 감사 로그 추가 필요",
                        await _proof(sf, b_pid), thread_root_id=trigger["id"])
            assert await dispatch_peer_ask_groups(sf, None) == 1

        b_row = await _target_for(sf, b_pid)
        assert b_row.state == "completed"
        async with sf() as db:
            reply = await db.get(Message, b_row.reply_message_id)
            a_group = await db.get(PeerAskGroup, b_row.group_id)
            wake = await db.get(Message, (await db.get(AgentTurn, a_group.wake_request_id)).trigger_message_id)
        assert reply.content == "B 최종: 감사 로그 추가 필요"
        assert "B 최종: 감사 로그 추가 필요" in wake.content
        assert a_group.state == "woken" and a_group.chain_id == a_group.id

    @pytest.mark.asyncio
    async def test_asking_an_agent_higher_up_the_chain_is_refused(self, ws_env) -> None:
        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        a_token, a_pid, b_token, b_pid, _c = await _nested_setup(ws_env)

        with TestClient(app) as client:
            _user_send(client, room.id, ws_env["token"], f"<@user:{a_pid}> 점검")
            _ask_peer_raw(client, a_token, room.id, [{"participant_id": b_pid, "question": "검토"}])
            _agent_send(client, room.id, a_token, "A 초안", await _proof(sf, a_pid))
            result = _ask_peer_raw(client, b_token, room.id, [
                {"participant_id": a_pid, "question": "되묻기"},
            ])

        assert result["structuredContent"]["status"] == "rejected"
        assert result["structuredContent"]["targets"][0]["reason"] == "cycle"
        assert "in a circle" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_depth_cap_hands_the_request_back(self, ws_env, monkeypatch) -> None:
        monkeypatch.setenv("ANYGARDEN_ASK_PEER_MAX_DEPTH", "1")
        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        a_token, a_pid, b_token, b_pid, c = await _nested_setup(ws_env)

        with TestClient(app) as client:
            _user_send(client, room.id, ws_env["token"], f"<@user:{a_pid}> 점검")
            _ask_peer_raw(client, a_token, room.id, [{"participant_id": b_pid, "question": "검토"}])
            _agent_send(client, room.id, a_token, "A 초안", await _proof(sf, a_pid))
            result = _ask_peer_raw(client, b_token, room.id, [{"agent_id": c, "question": "q"}])

        assert result["structuredContent"]["status"] == "forwarded"
        assert await _participant_for(sf, room.id, c) is None

    @pytest.mark.asyncio
    async def test_calls_cap_counts_the_whole_chain(self, ws_env, monkeypatch) -> None:
        monkeypatch.setenv("ANYGARDEN_ASK_PEER_MAX_CALLS", "1")
        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        a_token, a_pid, b_token, b_pid, c = await _nested_setup(ws_env)

        with TestClient(app) as client:
            _user_send(client, room.id, ws_env["token"], f"<@user:{a_pid}> 점검")
            _ask_peer_raw(client, a_token, room.id, [{"participant_id": b_pid, "question": "검토"}])
            _agent_send(client, room.id, a_token, "A 초안", await _proof(sf, a_pid))
            result = _ask_peer_raw(client, b_token, room.id, [{"agent_id": c, "question": "q"}])

        assert result["structuredContent"]["targets"][0]["reason"] == "limit_reached"


class TestChainApi:
    @pytest.mark.asyncio
    @pytest.mark.req("COL-17")
    async def test_chain_tree_and_cancel(self, ws_env) -> None:
        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        a_token, a_pid, b_token, b_pid, c = await _nested_setup(ws_env)

        with TestClient(app) as client:
            trigger = _user_send(client, room.id, ws_env["token"], f"<@user:{a_pid}> 점검")
            _ask_peer_raw(client, a_token, room.id, [{"participant_id": b_pid, "question": "검토"}])
            _agent_send(client, room.id, a_token, "A 초안", await _proof(sf, a_pid))
            _ask_peer_raw(client, b_token, room.id, [{"agent_id": c, "question": "감사 로그?"}])
            _agent_send(client, room.id, b_token, "B 초안", await _proof(sf, b_pid),
                        thread_root_id=trigger["id"])

            chains = client.get(
                f"/api/v1/rooms/{room.id}/peer-chains", headers=_auth(ws_env["token"])
            ).json()["chains"]
            assert len(chains) == 1
            chain = chains[0]
            assert chain["state"] == "active"
            assert chain["thread_root_id"] == trigger["id"]
            assert chain["origin_message_id"] == trigger["id"]
            assert (chain["max_depth"], chain["call_count"], chain["agent_count"]) == (2, 2, 3)
            assert chain["root"]["participant_id"] == a_pid
            nodes = {n["target_name"]: n for n in chain["nodes"]}
            assert nodes["peer"]["parent_id"] is None and nodes["peer"]["state"] == "waiting"
            assert nodes["감사 담당"]["parent_id"] == nodes["peer"]["id"]
            assert nodes["감사 담당"]["depth"] == 2 and nodes["감사 담당"]["state"] == "pending"
            assert nodes["감사 담당"]["caller_name"] == "peer"

            stopped = client.post(
                f"/api/v1/rooms/{room.id}/peer-chains/{chain['id']}/cancel",
                headers=_auth(ws_env["token"]),
            )
            assert stopped.status_code == 200, stopped.text
            assert stopped.json()["cancelled_turns"] == 1  # C's pending turn
            assert await dispatch_peer_ask_groups(sf, None) == 0
            after = client.get(
                f"/api/v1/rooms/{room.id}/peer-chains", headers=_auth(ws_env["token"])
            ).json()["chains"][0]

        assert after["state"] == "cancelled"
        assert {n["state"] for n in after["nodes"]} == {"cancelled"}
        c_pid = (await _participant_for(sf, room.id, c)).id
        assert await _open_turn(sf, c_pid) is None

    @pytest.mark.asyncio
    async def test_unknown_chain_is_404(self, ws_env) -> None:
        with TestClient(ws_env["app"]) as client:
            resp = client.post(
                f"/api/v1/rooms/{ws_env['room'].id}/peer-chains/nope/cancel",
                headers=_auth(ws_env["token"]),
            )
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_adding_a_thread_only_agent_makes_it_a_member(ws_env) -> None:
    from anygarden.rooms.membership import ensure_agent_in_room
    from anygarden.orchestration.agent_links import seat_linked_agent

    sf, room = ws_env["session_factory"], ws_env["room"]
    outside = await _add_outside_agent(sf, "보안 담당")
    async with sf() as db:
        await seat_linked_agent(db, room_id=room.id, agent_id=outside)
        await db.commit()
        participant, created = await ensure_agent_in_room(
            db, None, room_id=room.id, agent_id=outside
        )

    assert created is True and participant.joined_via is None

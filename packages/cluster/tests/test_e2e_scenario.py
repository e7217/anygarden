"""
E2E 시나리오 테스트: 실제 사용 흐름 검증

1. 서버 실행 (in-memory)
2. 유저 생성 + JWT 발급
3. 프로젝트/룸 생성 (REST API)
4. 에이전트 생성 + 토큰 발급
5. 유저 & 에이전트를 룸에 참여시킴 (REST API)
6. 유저 WebSocket 연결 → 메시지 송수신
7. 에이전트 WebSocket 연결 → 메시지 송수신
8. 5턴 대화 (유저 → 에이전트 → 유저 → ... 실시간 브로드캐스트)
"""

from __future__ import annotations

import asyncio
import json
import queue
import secrets
import threading
from typing import Any

import pytest
import pytest_asyncio
from starlette.testclient import TestClient

from anygarden.app import create_app
from anygarden.auth.jwt import create_user_token
from anygarden.auth.token import generate_token, hash_agent_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import (
    Agent,
    AgentToken,
    Base,
    Machine,
    MachineToken,
    Participant,
    Project,
    Room,
    User,
)
from anygarden.auth.machine_token import generate_machine_token, hash_machine_token


@pytest_asyncio.fixture()
async def e2e_env():
    """Full E2E environment: server + user + agent + room + participants."""
    config = AnygardenSettings(
        db_url="sqlite+aiosqlite://",
        jwt_secret=secrets.token_urlsafe(32),
        log_level="WARNING",
    )
    engine = build_engine(config.db_url)
    session_factory = build_session_factory(engine)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        # 1. 유저 생성
        user = User(email="alice@anygarden.io", password_hash="hashed_pw", is_admin=True)
        db.add(user)
        await db.flush()

        # 2. 에이전트 생성
        agent = Agent(name="PM", engine="claude-code", desired_state="running", actual_state="running")
        db.add(agent)
        await db.flush()

        # 3. 에이전트 토큰 발급
        agent_token_plain = generate_token()
        token_hash, lookup_hint = hash_agent_token(agent_token_plain)
        agent_token_record = AgentToken(
            agent_id=agent.id,
            token_hash=token_hash,
            lookup_hint=lookup_hint,
        )
        db.add(agent_token_record)

        # 4. 프로젝트 생성
        project = Project(name="Sprint-42")
        db.add(project)
        await db.flush()

        # 5. 룸 생성
        room = Room(project_id=project.id, name="main-chat")
        db.add(room)
        await db.flush()

        # 6. 유저를 룸에 참여시킴
        user_participant = Participant(room_id=room.id, user_id=user.id, role="admin")
        db.add(user_participant)
        await db.flush()

        # 7. 에이전트를 룸에 참여시킴
        agent_participant = Participant(room_id=room.id, agent_id=agent.id, role="member")
        db.add(agent_participant)
        await db.flush()

        # 8. 머신 등록 (Machine Token으로)
        machine = Machine(
            name="alice-laptop",
            hostname="localhost",
            owner_user_id=user.id,
            status="online",
            max_agents=4,
            cpu_cores=8,
            memory_gb=16.0,
        )
        db.add(machine)
        await db.flush()

        machine_token_plain = generate_machine_token()
        mch_hash, mch_hint = hash_machine_token(machine_token_plain)
        machine_token_record = MachineToken(
            machine_id=machine.id,
            token_hash=mch_hash,
            lookup_hint=mch_hint,
        )
        db.add(machine_token_record)

        await db.commit()

        # JWT 토큰 발급
        user_jwt = create_user_token(
            user.id, user.email, user.is_admin, secret=config.jwt_secret
        )

        # Refresh to get committed state
        for obj in [user, agent, project, room, user_participant, agent_participant, machine]:
            await db.refresh(obj)

        # App 생성
        app = create_app(config)
        app.state.engine = engine
        app.state.session_factory = session_factory

        yield {
            "app": app,
            "config": config,
            "user": user,
            "agent": agent,
            "project": project,
            "room": room,
            "user_participant": user_participant,
            "agent_participant": agent_participant,
            "machine": machine,
            "user_jwt": user_jwt,
            "agent_token": agent_token_plain,
            "machine_token": machine_token_plain,
        }

    await engine.dispose()


class TestE2EScenario:
    """End-to-end scenario: full user journey."""

    # ── Step 1: 서버 healthcheck ─────────────────────────────────────

    @pytest.mark.asyncio
    async def test_step1_server_running(self, e2e_env) -> None:
        """서버가 정상 응답하는지 확인 (#445 — 의존성 체크 healthz)."""
        with TestClient(e2e_env["app"]) as tc:
            resp = tc.get("/healthz")
            assert resp.status_code == 200
            body = resp.json()
            # ok 또는 degraded(논크리티컬 크론 종료) 모두 200·정상 부팅으로 간주.
            assert body["status"] in {"ok", "degraded"}
            # DB는 in-memory sqlite로 reachable ⇒ ok.
            assert body["components"]["db"] == "ok"
            # gateway 플래그 off ⇒ supervisor None ⇒ disabled (unhealthy 아님).
            assert body["components"]["gateway"] == "disabled"
            print(f"✓ Step 1: 서버 정상 실행 (status={body['status']})")

    # ── Step 2: REST API로 머신 목록 조회 ─────────────────────────────

    @pytest.mark.asyncio
    async def test_step2_machine_registered(self, e2e_env) -> None:
        """등록된 머신이 REST API로 조회되는지 확인."""
        with TestClient(e2e_env["app"]) as tc:
            resp = tc.get(
                "/api/v1/machines",
                headers={"Authorization": f"Bearer {e2e_env['user_jwt']}"},
            )
            assert resp.status_code == 200
            machines = resp.json()
            assert len(machines) >= 1
            assert machines[0]["name"] == "alice-laptop"
            assert machines[0]["status"] == "online"
            print(f"✓ Step 2: 머신 '{machines[0]['name']}' 등록 확인 (status={machines[0]['status']})")

    # ── Step 3: REST API로 룸 조회 ───────────────────────────────────

    @pytest.mark.asyncio
    async def test_step3_room_with_participants(self, e2e_env) -> None:
        """룸과 참여자가 REST API로 조회되는지 확인."""
        room_id = e2e_env["room"].id
        with TestClient(e2e_env["app"]) as tc:
            resp = tc.get(
                f"/api/v1/rooms/{room_id}",
                headers={"Authorization": f"Bearer {e2e_env['user_jwt']}"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["name"] == "main-chat"
            assert len(data["participants"]) == 2

            roles = {p["role"] for p in data["participants"]}
            assert "admin" in roles
            assert "member" in roles
            print(f"✓ Step 3: 룸 '{data['name']}' 참여자 {len(data['participants'])}명 확인")

    # ── Step 4: 유저 WebSocket 단독 연결 + 메시지 송수신 ──────────────

    @pytest.mark.asyncio
    async def test_step4_user_ws_send_receive(self, e2e_env) -> None:
        """유저가 WebSocket으로 메시지를 보내고 받을 수 있는지 확인."""
        room_id = e2e_env["room"].id
        token = e2e_env["user_jwt"]

        with TestClient(e2e_env["app"]) as tc:
            with tc.websocket_connect(
                f"/ws/rooms/{room_id}",
                subprotocols=["anygarden.v1", f"bearer.{token}"],
            ) as ws:
                welcome = json.loads(ws.receive_text())
                assert welcome["type"] == "welcome"
                ws.send_text(json.dumps({"type": "send", "content": "유저 테스트 메시지"}))
                resp = json.loads(ws.receive_text())
                assert resp["type"] == "message"
                assert resp["content"] == "유저 테스트 메시지"
                assert resp["seq"] == 1
                print(f"✓ Step 4: 유저 WS 연결 성공, 메시지 seq={resp['seq']}")

    # ── Step 5: 에이전트 WebSocket 연결 + 토큰 인증 ───────────────────

    @pytest.mark.asyncio
    async def test_step5_agent_ws_send_receive(self, e2e_env) -> None:
        """에이전트가 API Token으로 WebSocket 연결하고 메시지를 보낼 수 있는지 확인."""
        room_id = e2e_env["room"].id
        agent_token = e2e_env["agent_token"]

        with TestClient(e2e_env["app"]) as tc:
            with tc.websocket_connect(
                f"/ws/rooms/{room_id}",
                subprotocols=["anygarden.v1", f"bearer.{agent_token}"],
            ) as ws:
                welcome = json.loads(ws.receive_text())
                assert welcome["type"] == "welcome"
                ws.send_text(json.dumps({"type": "send", "content": "에이전트 PM 응답입니다"}))
                resp = json.loads(ws.receive_text())
                assert resp["type"] == "message"
                assert resp["content"] == "에이전트 PM 응답입니다"
                print(f"✓ Step 5: 에이전트 WS 연결 성공, 토큰 인증 확인, seq={resp['seq']}")

    # ── Step 6: 5턴 대화 (유저 ↔ 에이전트, 메시지 영속성 기반) ──────────

    @pytest.mark.asyncio
    async def test_step6_five_turn_conversation(self, e2e_env) -> None:
        """유저와 에이전트가 같은 룸에서 5턴 대화.

        각 참여자는 자기가 마지막으로 본 seq를 기억하고,
        재연결 시 since_seq로 놓친 메시지를 복구합니다.
        """
        room_id = e2e_env["room"].id
        user_jwt = e2e_env["user_jwt"]
        agent_token = e2e_env["agent_token"]

        conversation = [
            ("user", "안녕하세요, PM 에이전트님. Sprint-42 진행 상황이 어떤가요?"),
            ("agent", "안녕하세요! 현재 Sprint-42는 70% 진행되었습니다."),
            ("user", "프론트엔드 예상 완료일은 언제인가요?"),
            ("agent", "프론트엔드 팀에서 3일 내 완료 예정입니다."),
            ("user", "알겠습니다. 디자인 리뷰 일정을 내일로 잡아주세요."),
        ]

        # 각 참여자별 마지막으로 본 seq 추적
        user_last_seq = 0
        agent_last_seq = 0

        with TestClient(e2e_env["app"]) as tc:
            for turn_idx, (sender, content) in enumerate(conversation):
                if sender == "user":
                    token = user_jwt
                    my_last_seq = user_last_seq
                else:
                    token = agent_token
                    my_last_seq = agent_last_seq

                url = f"/ws/rooms/{room_id}"
                if my_last_seq > 0:
                    url += f"?since_seq={my_last_seq}"

                with tc.websocket_connect(
                    url,
                    subprotocols=["anygarden.v1", f"bearer.{token}"],
                ) as ws:
                    welcome = json.loads(ws.receive_text())
                    assert welcome["type"] == "welcome"
                    # since_seq > 0이면 놓친 메시지들을 먼저 수신
                    if my_last_seq > 0:
                        # 상대방이 보낸 메시지 리플레이 수신
                        replayed = json.loads(ws.receive_text())
                        assert replayed["type"] == "message"
                        prev_sender, prev_content = conversation[turn_idx - 1]
                        assert replayed["content"] == prev_content
                        role = "유저" if prev_sender == "user" else "에이전트(PM)"
                        print(f"  ← [{sender}] 재연결 → '{role}'의 메시지 수신 (seq={replayed['seq']})")

                    # 이번 턴의 메시지 전송
                    ws.send_text(json.dumps({"type": "send", "content": content}))
                    echo = json.loads(ws.receive_text())
                    assert echo["type"] == "message"
                    assert echo["content"] == content
                    current_seq = echo["seq"]

                    # 내 last_seq 업데이트
                    if sender == "user":
                        user_last_seq = current_seq
                    else:
                        agent_last_seq = current_seq

                    role = "유저" if sender == "user" else "에이전트(PM)"
                    print(f"  → [Turn {turn_idx+1}] {role}: {content[:55]}... (seq={current_seq})")

        assert user_last_seq == 5  # 유저가 마지막 메시지 (seq=5)
        assert agent_last_seq == 4  # 에이전트 마지막 메시지 (seq=4)
        print(f"\n✓ Step 6: 5턴 대화 완료!")
        print(f"  - 유저 발신 3건 (seq 1,3,5), 에이전트 발신 2건 (seq 2,4)")
        print(f"  - since_seq 기반 메시지 복구 4회 성공")

    # ── Step 7: 메시지 히스토리 REST API 검증 ──────────────────────────

    @pytest.mark.asyncio
    async def test_step7_message_history(self, e2e_env) -> None:
        """대화 후 메시지 히스토리가 REST API로 조회되는지 확인."""
        room_id = e2e_env["room"].id
        user_jwt = e2e_env["user_jwt"]

        # 먼저 몇 개 메시지를 생성
        with TestClient(e2e_env["app"]) as tc:
            with tc.websocket_connect(
                f"/ws/rooms/{room_id}",
                subprotocols=["anygarden.v1", f"bearer.{user_jwt}"],
            ) as ws:
                welcome = json.loads(ws.receive_text())
                assert welcome["type"] == "welcome"
                for i in range(3):
                    ws.send_text(json.dumps({"type": "send", "content": f"History msg {i+1}"}))
                    ws.receive_text()  # consume echo

            # REST API로 히스토리 조회
            resp = tc.get(
                f"/api/v1/rooms/{room_id}/messages?since_seq=0&limit=50",
                headers={"Authorization": f"Bearer {user_jwt}"},
            )
            assert resp.status_code == 200
            messages = resp.json()
            assert len(messages) >= 3
            print(f"✓ Step 7: 메시지 히스토리 {len(messages)}건 조회 확인")

    # ── Step 8: since_seq 재연결 복구 ────────────────────────────────

    @pytest.mark.asyncio
    async def test_step8_reconnect_since_seq(self, e2e_env) -> None:
        """연결 끊김 후 since_seq로 놓친 메시지 복구 확인."""
        room_id = e2e_env["room"].id
        user_jwt = e2e_env["user_jwt"]

        with TestClient(e2e_env["app"]) as tc:
            # 첫 연결: 3개 메시지 전송
            with tc.websocket_connect(
                f"/ws/rooms/{room_id}",
                subprotocols=["anygarden.v1", f"bearer.{user_jwt}"],
            ) as ws:
                welcome = json.loads(ws.receive_text())
                assert welcome["type"] == "welcome"
                last_seq = 0
                for i in range(3):
                    ws.send_text(json.dumps({"type": "send", "content": f"Before disconnect {i+1}"}))
                    data = json.loads(ws.receive_text())
                    last_seq = data["seq"]

            # 재연결: since_seq로 놓친 메시지 수신
            with tc.websocket_connect(
                f"/ws/rooms/{room_id}?since_seq={last_seq - 1}",
                subprotocols=["anygarden.v1", f"bearer.{user_jwt}"],
            ) as ws:
                welcome = json.loads(ws.receive_text())
                assert welcome["type"] == "welcome"
                # since_seq=last_seq-1 이므로 마지막 1개 메시지가 리플레이됨
                replayed = json.loads(ws.receive_text())
                assert replayed["type"] == "message"
                assert replayed["seq"] == last_seq
                assert "Before disconnect 3" in replayed["content"]
                print(f"✓ Step 8: since_seq={last_seq-1} 재연결 → seq={replayed['seq']} 복구 확인")


# ── #445 — 의존성 체크 healthz 핸들러 단위 테스트 ──────────────────────
#
# 핸들러를 격리해서 검증한다. TestClient를 ``with`` 없이 사용하면
# lifespan(백그라운드 크론·goal scheduler)이 돌지 않으므로, ``app.state``를
# 원하는 값으로 직접 세팅해 각 분기(DB up, 게이트웨이 FAILED, None 태스크)를
# 정밀하게 찍어볼 수 있다.


class _FakeSupervisor:
    """``state`` 프로퍼티만 흉내내는 게이트웨이 슈퍼바이저 더블."""

    def __init__(self, state) -> None:
        self._state = state

    @property
    def state(self):
        return self._state


@pytest.fixture()
def healthz_app():
    """Lifespan 없이 healthz만 때리기 위한 앱 + in-memory 세션 팩토리."""
    config = AnygardenSettings(
        db_url="sqlite+aiosqlite://",
        jwt_secret=secrets.token_urlsafe(32),
        log_level="WARNING",
    )
    engine = build_engine(config.db_url)
    session_factory = build_session_factory(engine)
    app = create_app(config)
    # lifespan을 돌리지 않으므로 healthz가 보는 의존성만 직접 주입한다.
    app.state.session_factory = session_factory
    return app


class TestHealthzDependencyCheck:
    """#445 — healthz가 실제 의존성 상태를 반영하는지 검증."""

    @pytest.mark.asyncio
    async def test_db_up_returns_200_with_components(self, healthz_app) -> None:
        """DB reachable ⇒ 200 + components 블록 (db=ok)."""
        # 게이트웨이/크론 미설정 ⇒ 모두 disabled, 크리티컬 다운 없음.
        tc = TestClient(healthz_app)
        resp = tc.get("/healthz")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["components"]["db"] == "ok"
        assert body["components"]["gateway"] == "disabled"
        # None 태스크는 unhealthy가 아니라 disabled.
        assert body["components"]["orphan_sweeper"] == "disabled"
        assert body["components"]["span_reaper"] == "disabled"

    @pytest.mark.asyncio
    async def test_gateway_failed_returns_503(self, healthz_app) -> None:
        """게이트웨이 슈퍼바이저 FAILED ⇒ 503 unhealthy."""
        from anygarden.llm_gateway.supervisor import GatewayState

        healthz_app.state.llm_gateway_supervisor = _FakeSupervisor(
            GatewayState.FAILED
        )
        tc = TestClient(healthz_app)
        resp = tc.get("/healthz")
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "unhealthy"
        assert body["components"]["gateway"] == "unhealthy"
        # DB는 여전히 살아 있다.
        assert body["components"]["db"] == "ok"

    @pytest.mark.asyncio
    async def test_gateway_crashed_returns_200_degraded(self, healthz_app) -> None:
        """게이트웨이 CRASHED(일시적) ⇒ 200 degraded, 503 아님."""
        from anygarden.llm_gateway.supervisor import GatewayState

        healthz_app.state.llm_gateway_supervisor = _FakeSupervisor(
            GatewayState.CRASHED
        )
        tc = TestClient(healthz_app)
        resp = tc.get("/healthz")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "degraded"
        assert body["components"]["gateway"] == "degraded"

    @pytest.mark.asyncio
    async def test_none_tasks_treated_as_disabled(self, healthz_app) -> None:
        """None인 백그라운드 태스크는 disabled로 취급(unhealthy 아님)."""
        healthz_app.state.orphan_sweeper_task = None
        healthz_app.state.span_reaper_task = None
        healthz_app.state.goal_scheduler = None
        tc = TestClient(healthz_app)
        resp = tc.get("/healthz")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["components"]["orphan_sweeper"] == "disabled"
        assert body["components"]["span_reaper"] == "disabled"
        assert body["components"]["goal_scheduler"] == "disabled"

    @pytest.mark.asyncio
    async def test_done_task_marks_degraded(self, healthz_app) -> None:
        """이미 종료된(.done()=True) 태스크는 unhealthy ⇒ degraded(200)."""

        async def _noop() -> None:
            return None

        task = asyncio.ensure_future(_noop())
        await task  # 즉시 완료시켜 done()=True 상태로 만든다.
        healthz_app.state.orphan_sweeper_task = task
        tc = TestClient(healthz_app)
        resp = tc.get("/healthz")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "degraded"
        assert body["components"]["orphan_sweeper"] == "unhealthy"

"""
실제 LLM 대화 E2E 테스트

호스트 머신의 codex CLI를 subprocess로 호출하여
진짜 AI 응답이 포함된 5턴 대화를 검증합니다.

기본 ``pytest`` 실행에서는 pyproject 의 ``addopts = "-m 'not slow'"``
로 건너뜁니다. 수동으로 실행하려면::

    cd anygarden-server && uv run pytest -m slow tests/test_e2e_real_conversation.py
"""

from __future__ import annotations

import json
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio
from starlette.testclient import TestClient

from anygarden.app import create_app
from anygarden.auth.jwt import create_user_token
from anygarden.auth.token import generate_token, hash_agent_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import (
    Agent, AgentToken, Base, Participant, Project, Room, User,
)

CODEX_PATH = shutil.which("codex")
requires_codex = pytest.mark.skipif(CODEX_PATH is None, reason="codex not installed")


def call_codex(prompt: str) -> str:
    """Synchronously call `codex exec` and return response text.

    The ``-o <path>`` flag writes the assistant reply to a file. codex
    under its default ``workspace-write`` sandbox rejects any write
    whose target falls outside the walked-up project root, so we put
    the temp file inside the current working directory (which is the
    pytest runner's cwd — ``anygarden-server/`` — and therefore inside
    the monorepo project root that codex discovers via ``.git``
    upward traversal). ``tempfile.NamedTemporaryFile`` default is
    ``/tmp``, which is outside every plausible project root and
    causes codex to hang until the subprocess timeout.
    """
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".txt",
        delete=False,
        prefix="anygarden-e2e-",
        dir=str(Path.cwd()),
    ) as f:
        out_path = f.name

    result = subprocess.run(
        [CODEX_PATH, "exec", prompt, "--ephemeral",
         "--skip-git-repo-check", "-o", out_path],
        capture_output=True, text=True, timeout=120,
    )
    text = Path(out_path).read_text().strip()
    Path(out_path).unlink(missing_ok=True)

    if result.returncode != 0:
        raise RuntimeError(f"codex failed: {result.stderr[:300]}")
    return text


@pytest_asyncio.fixture()
async def env():
    config = AnygardenSettings(
        db_url="sqlite+aiosqlite://",
        jwt_secret=secrets.token_urlsafe(32),
        log_level="WARNING",
    )
    engine = build_engine(config.db_url)
    sf = build_session_factory(engine)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with sf() as db:
        user = User(email="alice@anygarden.io", password_hash="x", is_admin=True)
        db.add(user)
        await db.flush()

        agent = Agent(name="PM-Codex", engine="codex")
        db.add(agent)
        await db.flush()

        tok = generate_token()
        th, hint = hash_agent_token(tok)
        db.add(AgentToken(agent_id=agent.id, token_hash=th, lookup_hint=hint))

        proj = Project(name="Sprint-42")
        db.add(proj)
        await db.flush()

        room = Room(project_id=proj.id, name="main-chat")
        db.add(room)
        await db.flush()

        db.add(Participant(room_id=room.id, user_id=user.id, role="admin"))
        db.add(Participant(room_id=room.id, agent_id=agent.id, role="member"))
        await db.commit()
        await db.refresh(room)

        jwt = create_user_token(user.id, user.email, True, secret=config.jwt_secret)

        app = create_app(config)
        app.state.engine = engine
        app.state.session_factory = sf

        yield {"app": app, "room_id": room.id, "jwt": jwt, "agt_tok": tok}

    await engine.dispose()


@pytest.mark.slow
@requires_codex
class TestRealConversation:

    @pytest.mark.asyncio
    async def test_five_turns_with_codex(self, env) -> None:
        app, room_id = env["app"], env["room_id"]
        user_jwt, agent_tok = env["jwt"], env["agt_tok"]

        user_msgs = [
            "안녕하세요! Sprint-42에서 가장 중요한 우선순위 3가지를 간단히 제안해주세요.",
            "좋습니다. 첫 번째 항목의 실행 계획을 한 문장으로 알려주세요.",
            "감사합니다. 이 스프린트의 주요 리스크 하나만 짚어주세요.",
        ]

        log: list[dict] = []
        user_seq = 0
        agent_seq = 0

        print("\n" + "=" * 60)
        print("  실제 LLM 대화 E2E (codex CLI)")
        print("=" * 60)

        with TestClient(app) as tc:
            for turn in range(5):
                is_user = (turn % 2 == 0)

                if is_user:
                    # ── 유저 턴 ──
                    content = user_msgs[turn // 2]
                    url = f"/ws/rooms/{room_id}"
                    if user_seq > 0:
                        url += f"?since_seq={user_seq}"

                    with tc.websocket_connect(
                        url, subprotocols=["anygarden.v1", f"bearer.{user_jwt}"],
                    ) as ws:
                        welcome = json.loads(ws.receive_text())
                        assert welcome["type"] == "welcome"
                        if user_seq > 0:
                            rep = json.loads(ws.receive_text())
                            print(f"\n  ← [유저 재연결] 에이전트 응답 수신 (seq={rep['seq']})")

                        ws.send_text(json.dumps({"type": "send", "content": content}))
                        echo = json.loads(ws.receive_text())
                        user_seq = echo["seq"]

                    log.append({"turn": turn + 1, "role": "user", "content": content, "seq": user_seq})
                    print(f"\n  [Turn {turn+1}] 유저: {content}")

                else:
                    # ── 에이전트 턴 (codex 실제 호출) ──
                    ctx = [
                        "You are PM-Codex, a project management AI in a team chat.",
                        "Project: Sprint-42. Reply concisely in Korean.",
                        "Do NOT run commands or use tools. Text reply only.", ""
                    ]
                    for e in log:
                        r = "유저" if e["role"] == "user" else "PM-Codex"
                        ctx.append(f"[{r}] {e['content']}")
                    ctx += ["", "PM-Codex:"]

                    print(f"\n  [Turn {turn+1}] 에이전트: codex 호출 중...")
                    response = call_codex("\n".join(ctx))
                    assert response, "codex가 빈 응답을 반환했습니다"

                    # 에이전트 WS로 전송
                    url = f"/ws/rooms/{room_id}"
                    if agent_seq > 0:
                        url += f"?since_seq={agent_seq}"

                    with tc.websocket_connect(
                        url, subprotocols=["anygarden.v1", f"bearer.{agent_tok}"],
                    ) as ws:
                        welcome = json.loads(ws.receive_text())
                        assert welcome["type"] == "welcome"
                        rep = json.loads(ws.receive_text())  # 유저 메시지 수신

                        ws.send_text(json.dumps({"type": "send", "content": response}))
                        echo = json.loads(ws.receive_text())
                        agent_seq = echo["seq"]

                    log.append({"turn": turn + 1, "role": "agent", "content": response, "seq": agent_seq})
                    # 응답이 여러 줄일 수 있으므로 첫 200자만 표시
                    preview = response.replace("\n", " ")[:200]
                    print(f"  [Turn {turn+1}] 에이전트(PM-Codex): {preview}")

            # 히스토리 검증
            resp = tc.get(
                f"/api/v1/rooms/{room_id}/messages?since_seq=0&limit=50",
                headers={"Authorization": f"Bearer {user_jwt}"},
            )
            history = resp.json()

        assert len(log) == 5
        assert len(history) == 5

        print("\n" + "=" * 60)
        print("  ✓ 5턴 실제 대화 완료!")
        print(f"  - 유저 3건 + 에이전트(codex) 2건 = 총 {len(history)}건 DB 영속화")
        print("=" * 60)
        print("\n── 전체 대화 로그 ──")
        for e in log:
            role = "유저" if e["role"] == "user" else "에이전트(PM-Codex)"
            print(f"\n  [{role}] (seq={e['seq']})")
            for line in e["content"].split("\n"):
                print(f"    {line}")

#!/usr/bin/env python3
"""
실제 LLM 대화 E2E 검증 스크립트

서버를 실제 프로세스로 기동하고, httpx + websockets로 통신하여
호스트의 codex CLI로 진짜 AI 응답 5턴 대화를 검증합니다.

Usage:
    cd doorae-server && uv run python scripts/e2e_real_chat.py
"""

import asyncio
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def call_codex(prompt: str) -> str:
    """codex exec → 텍스트 응답."""
    codex = shutil.which("codex")
    if not codex:
        sys.exit("ERROR: codex not found")

    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, prefix="doorae-") as f:
        out = f.name

    r = subprocess.run(
        [codex, "exec", prompt, "--ephemeral", "--skip-git-repo-check", "-o", out],
        capture_output=True, text=True, timeout=120,
    )
    text = Path(out).read_text().strip()
    Path(out).unlink(missing_ok=True)
    if r.returncode != 0:
        sys.exit(f"codex failed: {r.stderr[:300]}")
    return text


async def run_e2e():
    import httpx
    import websockets

    # ── 1. 서버 기동 ──
    db_dir = tempfile.mkdtemp(prefix="doorae-e2e-")
    db_path = Path(db_dir) / "test.db"
    jwt_secret = secrets.token_urlsafe(32)
    port = 18742

    env = {
        **os.environ,
        "DOORAE_DB_URL": f"sqlite+aiosqlite:///{db_path}",
        "DOORAE_JWT_SECRET": jwt_secret,
        "DOORAE_LOG_LEVEL": "WARNING",
    }

    server_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "doorae.app:create_app",
         "--factory", "--host", "127.0.0.1", "--port", str(port),
         "--log-level", "warning"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # 서버 준비 대기
    base = f"http://127.0.0.1:{port}"
    ws_base = f"ws://127.0.0.1:{port}"
    ready = False
    async with httpx.AsyncClient() as http:
        for _ in range(30):
            try:
                r = await http.get(f"{base}/healthz")
                if r.status_code == 200:
                    ready = True
                    break
            except httpx.ConnectError:
                pass
            await asyncio.sleep(0.5)

    if not ready:
        server_proc.kill()
        sys.exit("ERROR: server failed to start")

    print("\n" + "=" * 65)
    print("  Doorae E2E: 실제 LLM 대화 (codex CLI)")
    print("=" * 65)
    print(f"  서버: {base} (pid={server_proc.pid})")

    try:
        async with httpx.AsyncClient(base_url=base) as http:
            # ── 2. DB 직접 셋업 (SQLAlchemy) ──
            from doorae.db.engine import build_engine, build_session_factory
            from doorae.db.models import (
                Agent, AgentToken, Base, Participant, Project, Room, User,
            )
            from doorae.auth.jwt import create_user_token
            from doorae.auth.token import generate_token, hash_agent_token

            engine = build_engine(f"sqlite+aiosqlite:///{db_path}")
            sf = build_session_factory(engine)

            # 서버가 이미 테이블을 만들었으므로 바로 데이터 삽입
            await asyncio.sleep(1)  # 서버가 migration 완료할 시간

            async with sf() as db:
                user = User(email="alice@doorae.io", password_hash="x", is_admin=True)
                db.add(user)
                await db.flush()

                agent = Agent(name="PM-Codex", engine="codex")
                db.add(agent)
                await db.flush()

                tok_plain = generate_token()
                th, hint = hash_agent_token(tok_plain)
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

                room_id = room.id
                user_jwt = create_user_token(user.id, user.email, True, secret=jwt_secret)

            await engine.dispose()

            print(f"  유저: alice@doorae.io (JWT)")
            print(f"  에이전트: PM-Codex (codex engine, API Token)")
            print(f"  룸: main-chat ({room_id[:8]}...)")

            # ── 3. 5턴 대화 ──
            user_msgs = [
                "안녕하세요! Sprint-42에서 가장 중요한 우선순위 3가지를 간단히 제안해주세요.",
                "좋습니다. 첫 번째 항목의 실행 계획을 한 문장으로 알려주세요.",
                "감사합니다. 이 스프린트의 주요 리스크 하나만 짚어주세요.",
            ]

            log = []
            user_seq = 0
            agent_seq = 0

            for turn in range(5):
                is_user = (turn % 2 == 0)

                if is_user:
                    content = user_msgs[turn // 2]
                    token = user_jwt
                    my_seq = user_seq
                else:
                    # codex 호출
                    ctx = [
                        "You are PM-Codex, a project management AI in a team chat.",
                        "Project: Sprint-42. Reply concisely in Korean (2-3 sentences max).",
                        "Do NOT run commands or use tools. Text reply only.", "",
                    ]
                    for e in log:
                        r = "유저" if e["role"] == "user" else "PM-Codex"
                        ctx.append(f"[{r}] {e['content']}")
                    ctx += ["", "PM-Codex의 답변:"]

                    print(f"\n  ⏳ [Turn {turn+1}] codex 호출 중...", end="", flush=True)
                    content = call_codex("\n".join(ctx))
                    print(" 완료!")

                    token = tok_plain
                    my_seq = agent_seq

                # WebSocket 연결
                url = f"{ws_base}/ws/rooms/{room_id}"
                if my_seq > 0:
                    url += f"?since_seq={my_seq}"

                async with websockets.connect(
                    url,
                    subprotocols=["doorae.v1", f"bearer.{token}"],
                ) as ws:
                    # since_seq > 0이면 놓친 메시지 수신
                    if my_seq > 0:
                        raw = await asyncio.wait_for(ws.recv(), timeout=5)
                        rep = json.loads(raw)
                        sender = "유저" if is_user else "에이전트"
                        prev_role = "에이전트" if is_user else "유저"
                        print(f"  ← [{sender} 재연결] {prev_role}의 메시지 수신 (seq={rep['seq']})")

                    # 메시지 전송
                    await ws.send(json.dumps({"type": "send", "content": content}))
                    raw = await asyncio.wait_for(ws.recv(), timeout=5)
                    echo = json.loads(raw)
                    seq = echo["seq"]

                if is_user:
                    user_seq = seq
                    log.append({"turn": turn + 1, "role": "user", "content": content, "seq": seq})
                    print(f"\n  → [Turn {turn+1}] 유저: {content}")
                else:
                    agent_seq = seq
                    log.append({"turn": turn + 1, "role": "agent", "content": content, "seq": seq})
                    preview = content.replace("\n", " ")[:200]
                    print(f"  → [Turn {turn+1}] 에이전트(PM-Codex): {preview}")

            # ── 4. 히스토리 검증 ──
            resp = await http.get(
                f"/api/v1/rooms/{room_id}/messages?since_seq=0&limit=50",
                headers={"Authorization": f"Bearer {user_jwt}"},
            )
            history = resp.json()

        # ── 결과 ──
        print("\n" + "=" * 65)
        ok = len(log) == 5 and len(history) == 5
        print(f"  {'✅' if ok else '❌'} 5턴 실제 대화 {'완료' if ok else '실패'}!")
        print(f"  - 유저 3건 + 에이전트(codex) 2건 = 총 {len(history)}건 DB 영속화")
        print(f"  - since_seq 재연결 복구: {sum(1 for e in log if e['seq'] > 1) - 1}회")
        print("=" * 65)

        print("\n── 전체 대화 로그 ──")
        for e in log:
            role = "👤 유저" if e["role"] == "user" else "🤖 에이전트(PM-Codex)"
            print(f"\n  {role}  (seq={e['seq']})")
            for line in e["content"].split("\n"):
                print(f"    {line}")
        print()

    finally:
        server_proc.terminate()
        try:
            server_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server_proc.kill()
        # 임시 DB 정리
        import shutil as sh
        sh.rmtree(db_dir, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(run_e2e())

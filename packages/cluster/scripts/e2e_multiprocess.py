#!/usr/bin/env python3
"""
멀티프로세스 E2E: 서버 + 데몬 + 에이전트(자동 spawn) + 5턴 대화

Usage: cd anygarden-server && uv run python scripts/e2e_multiprocess.py
"""
import asyncio, json, os, secrets, shutil, subprocess, sys, tempfile, time
from pathlib import Path

async def run():
    import httpx, websockets

    db_dir = tempfile.mkdtemp(prefix="anygarden-e2e-")
    db_path = Path(db_dir) / "e2e.db"
    jwt_secret = secrets.token_urlsafe(32)
    port = 18744
    base, ws_base = f"http://127.0.0.1:{port}", f"ws://127.0.0.1:{port}"
    procs = []

    # anygarden-agent가 SDK venv에 설치되어 있으므로 PATH에 추가
    sdk_bin = str(Path(__file__).resolve().parent.parent.parent / "anygarden-sdk" / ".venv" / "bin")
    env = {**os.environ,
        "PATH": f"{sdk_bin}:{os.environ.get('PATH', '')}",
        "ANYGARDEN_DB_URL": f"sqlite+aiosqlite:///{db_path}",
        "ANYGARDEN_JWT_SECRET": jwt_secret, "ANYGARDEN_LOG_LEVEL": "WARNING",
        "ANYGARDEN_HOST": "127.0.0.1", "ANYGARDEN_PORT": str(port)}

    try:
        # ① 서버 기동
        server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "anygarden.app:create_app",
             "--factory", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        procs.append(server)

        async with httpx.AsyncClient() as http:
            for _ in range(30):
                try:
                    if (await http.get(f"{base}/healthz")).status_code == 200: break
                except httpx.ConnectError: pass
                await asyncio.sleep(0.5)
            else: sys.exit("서버 시작 실패")

        print(f"\n{'='*65}\n  Anygarden 멀티프로세스 E2E\n{'='*65}")
        print(f"  ✓ ① 서버 기동 (pid={server.pid})")

        # DB 접근용
        from anygarden.db.engine import build_engine, build_session_factory
        from anygarden.db.models import Base, User, Project, Room, Participant
        from anygarden.auth.jwt import create_user_token
        engine = build_engine(f"sqlite+aiosqlite:///{db_path}")
        sf = build_session_factory(engine)
        await asyncio.sleep(1)

        # ② 유저 생성
        async with sf() as db:
            user = User(email="alice@anygarden.io", password_hash="x", is_admin=True)
            db.add(user); await db.commit(); await db.refresh(user)
            user_jwt = create_user_token(user.id, user.email, True, secret=jwt_secret)
        headers = {"Authorization": f"Bearer {user_jwt}"}
        print(f"  ✓ ② 유저 생성")

        # ③ 머신 등록 (REST)
        async with httpx.AsyncClient(base_url=base) as http:
            resp = await http.post("/api/v1/machines", json={
                "name": os.uname().nodename, "hostname": os.uname().nodename, "max_agents": 4,
            }, headers=headers)
            assert resp.status_code == 201, f"머신 등록 실패: {resp.text}"
            machine_id = resp.json()["id"]
            machine_token = resp.json()["machine_token"]
        print(f"  ✓ ③ 머신 등록: {os.uname().nodename}")

        # ④ 데몬 프로세스 기동
        daemon_code = f'''
import asyncio, sys
sys.path.insert(0, "{Path(__file__).resolve().parent.parent.parent / "anygarden-machine"}")
from anygarden_machine.daemon import MachineDaemon
asyncio.run(MachineDaemon(
    server_url="{ws_base}/ws/machines/{machine_id}",
    machine_id="{machine_id}", machine_token="{machine_token}", max_agents=4,
).run())
'''
        daemon_path = Path(db_dir) / "daemon.py"
        daemon_path.write_text(daemon_code)
        daemon_log = Path(db_dir) / "daemon.log"
        daemon = subprocess.Popen(
            [sys.executable, str(daemon_path)], env=env,
            stdout=open(daemon_log, "w"), stderr=subprocess.STDOUT)
        procs.append(daemon)
        await asyncio.sleep(3)  # register + engine detect 대기

        async with httpx.AsyncClient(base_url=base) as http:
            machines = (await http.get("/api/v1/machines", headers=headers)).json()
            status = machines[0]["status"] if machines else "?"
        print(f"  ✓ ④ 데몬 기동 (pid={daemon.pid}, status={status})")

        # ⑤ 프로젝트/룸 생성
        async with sf() as db:
            proj = Project(name="Sprint-42"); db.add(proj); await db.flush()
            room = Room(project_id=proj.id, name="main-chat"); db.add(room); await db.flush()
            room_id = room.id
            db.add(Participant(room_id=room_id, user_id=user.id, role="admin"))
            await db.commit()
        print(f"  ✓ ⑤ 프로젝트/룸 생성")

        # ⑥ POST /api/v1/agents → 스케줄러 → 데몬에 spawn_agent
        async with httpx.AsyncClient(base_url=base) as http:
            resp = await http.post("/api/v1/agents", json={
                "name": "PM-Codex", "engine": "codex", "rooms": [room_id],
            }, headers=headers)
            if resp.status_code == 201:
                agent_id = resp.json()["id"]
                print(f"  ✓ ⑥ POST /api/v1/agents → 스케줄러 트리거")
            else:
                print(f"  ✗ ⑥ agents API 실패: {resp.status_code} {resp.text}")
                return

        # 에이전트 spawn 대기
        print(f"  ⏳ 에이전트 subprocess spawn 대기...", end="", flush=True)
        for i in range(15):
            await asyncio.sleep(2)
            async with sf() as db:
                from sqlalchemy import select
                from anygarden.db.models import Agent
                r = await db.execute(select(Agent).where(Agent.id == agent_id))
                a = r.scalar_one_or_none()
                if a and a.actual_state == "running":
                    print(f" running! (pid={a.pid})")
                    break
                print(".", end="", flush=True)
        else:
            async with sf() as db:
                r = await db.execute(select(Agent).where(Agent.id == agent_id))
                a = r.scalar_one_or_none()
                print(f"\n  ⚠ 에이전트 상태: {a.actual_state if a else 'not found'}")
                if a and a.last_crash_reason:
                    print(f"    crash reason: {a.last_crash_reason[:300]}")
            print(f"  데몬 로그 (마지막 20줄):")
            print(daemon_log.read_text()[-2000:])

        # ⑦ 5턴 대화
        print(f"\n  ── 5턴 대화 시작 ──\n")
        user_msgs = [
            "안녕하세요! Sprint-42에서 가장 중요한 우선순위 3가지를 간단히 제안해주세요.",
            "좋습니다. 첫 번째 항목의 실행 계획을 한 문장으로 알려주세요.",
            "감사합니다. 이 스프린트의 주요 리스크 하나만 짚어주세요.",
        ]
        log = []

        async with websockets.connect(
            f"{ws_base}/ws/rooms/{room_id}",
            subprotocols=[websockets.Subprotocol("anygarden.v1"),
                          websockets.Subprotocol(f"bearer.{user_jwt}")],
        ) as user_ws:
            for turn in range(5):
                if turn % 2 == 0:
                    content = user_msgs[turn // 2]
                    await user_ws.send(json.dumps({"type": "send", "content": content}))
                    echo = json.loads(await asyncio.wait_for(user_ws.recv(), timeout=10))
                    log.append({"turn": turn+1, "role": "user", "content": content, "seq": echo["seq"]})
                    print(f"  → [Turn {turn+1}] 유저: {content}")
                else:
                    print(f"  ⏳ [Turn {turn+1}] 에이전트 응답 대기...", end="", flush=True)
                    try:
                        raw = await asyncio.wait_for(user_ws.recv(), timeout=120)
                        data = json.loads(raw)
                        # typing 프레임 무시
                        while data.get("type") != "message":
                            raw = await asyncio.wait_for(user_ws.recv(), timeout=120)
                            data = json.loads(raw)
                        content = data["content"]
                        log.append({"turn": turn+1, "role": "agent", "content": content, "seq": data["seq"]})
                        print(f" 완료!")
                        print(f"  → [Turn {turn+1}] 에이전트(PM-Codex): {content.replace(chr(10),' ')[:200]}")
                    except asyncio.TimeoutError:
                        print(f" 타임아웃 (120초)")
                        print(f"\n  데몬 로그:")
                        print(daemon_log.read_text()[-3000:])
                        break

        await engine.dispose()

        # ⑧ 결과
        async with httpx.AsyncClient(base_url=base) as http:
            history = (await http.get(
                f"/api/v1/rooms/{room_id}/messages?since_seq=0&limit=50", headers=headers)).json()
            machines = (await http.get("/api/v1/machines", headers=headers)).json()

        print(f"\n{'='*65}")
        ok = len(log) == 5
        print(f"  {'✅' if ok else '⚠'} {len(log)}턴 완료 / DB {len(history)}건")
        print(f"  - 서버 pid={server.pid}, 데몬 pid={daemon.pid}")
        print(f"  - 머신: {machines[0]['name']} (status={machines[0]['status']})" if machines else "")
        print(f"{'='*65}")

        if log:
            print("\n── 전체 대화 로그 ──")
            for e in log:
                role = "👤 유저" if e["role"] == "user" else "🤖 에이전트(PM-Codex)"
                print(f"\n  {role}  (seq={e['seq']})")
                for line in e["content"].split("\n"):
                    print(f"    {line}")
        print()

    finally:
        for p in procs:
            try: p.terminate(); p.wait(timeout=5)
            except: pass
            try: p.kill()
            except: pass
        shutil.rmtree(db_dir, ignore_errors=True)

if __name__ == "__main__":
    asyncio.run(run())

# Week 4: anygarden-machine 패키지 (Machine Daemon)

> **목표**: `uvx anygarden-machine register` + `uvx anygarden-machine run`이 동작하는 Daemon
> **산출물**: `anygarden-machine/` 저장소, Daemon이 서버에 등록되고 spawn 명령을 수신 가능
> **정본 참조**: [10-machine-scheduler.md](../10-machine-scheduler.md) §10.3-§10.6, §10.9

---

## 1. 요약

Week 4는 **3번째 저장소 `anygarden-machine/`을 처음부터 생성**하고, Machine Daemon을 구현한다:
- 엔진 capability 자동 감지 (6종)
- 서버와 WebSocket(`/ws/machines/{id}`) 연결 + register + heartbeat
- `spawn_agent` 명령 수신 → `uvx anygarden-agent` subprocess spawn
- `kill_agent` 명령 수신 → subprocess 종료
- 자식 프로세스 watchdog (크래시 감지 + `agent_crashed` 보고)
- CLI: `register` / `run` / `status` / `install-systemd-unit`

Week 4 끝에 **Daemon이 서버에 "claude-code, codex 사용 가능"이라고 보고하고, 서버의 spawn 명령을 받아 실제로 에이전트 subprocess를 띄울 수 있어야** 한다. 단, 서버 측 스케줄러는 Week 5.

---

## 2. 생성할 파일

```
anygarden-machine/
├── pyproject.toml                           # §10.3 정본
├── README.md
├── LICENSE
├── anygarden_machine/
│   ├── __init__.py                          # __version__
│   ├── cli.py                               # [80 LOC] register/run/status/install-systemd-unit
│   ├── config.py                            # [30 LOC] ~/.anygarden/machine.toml + .token
│   ├── daemon.py                            # [90 LOC] WS 메인 루프 + heartbeat
│   ├── detector.py                          # [60 LOC] 6종 엔진 자동 감지
│   ├── spawner.py                           # [80 LOC] subprocess spawn/kill/watch
│   ├── supervisor.py                        # [30 LOC] 자식 프로세스 watchdog
│   └── protocol/
│       ├── __init__.py
│       └── frames.py                        # [40 LOC] Machine↔Server 프레임 Pydantic
└── tests/
    ├── conftest.py
    ├── test_detector.py                     # 엔진 감지 테스트
    ├── test_spawner.py                      # subprocess mock 테스트
    └── test_daemon.py                       # WS 연결 mock 테스트
```

**합계**: ~410 LOC (§10.3 예산과 일치)

---

## 3. 구현 단계

### Phase 4A: 프로젝트 스캐폴딩 + 설정 (Day 1 오전)

- [ ] `anygarden-machine/` 디렉토리 생성
- [ ] `pyproject.toml` 작성 (§10.3 정본: websockets, httpx, pydantic, click, structlog, psutil, pyyaml, argon2-cffi)
- [ ] `anygarden_machine/__init__.py` — `__version__ = "0.1.0"`
- [ ] `anygarden_machine/config.py`:
  - `~/.anygarden/machine.toml` 로드 (machine_id, name, server_url, limits)
  - `~/.anygarden/machine.token` 분리 파일 (chmod 600)
- [ ] `anygarden_machine/protocol/frames.py`:
  - `RegisterFrame`, `HeartbeatFrame`, `SpawnAgentFrame`, `KillAgentFrame`
  - `AgentStartedFrame`, `AgentSpawnFailedFrame`, `AgentStoppedFrame`, `AgentCrashedFrame`
  - 총 8+3 = 11종 프레임 (§10.8.1 표)
- [ ] **검증**: `pip install -e .` 성공

### Phase 4B: 엔진 감지 (Day 1 오후)

- [ ] `anygarden_machine/detector.py` (§10.4 코드 기반):
  - binary 감지: `claude-code --version`, `codex --version`, `openhands --version`
  - Python import 감지: `python -c "import deepagents; print(...)"`
  - 환경변수 감지: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`
  - 비동기 `asyncio.create_subprocess_exec` + 5초 타임아웃
  - 반환: `list[EngineInfo]` (engine, version, path)
- [ ] **검증**: `pytest tests/test_detector.py` — 실제 바이너리 없는 환경에서도 동작 (mock)

### Phase 4C: Daemon 메인 루프 (Day 2)

- [ ] `anygarden_machine/daemon.py` (§10.5 코드 기반):
  - `MachineDaemon.__init__(server_url, machine_id, machine_token, max_agents)`
  - `run()`: WebSocket 재연결 루프 (지수 백오프, 최대 60초)
  - `_register()`: register 프레임 전송 (capabilities 포함)
  - `_heartbeat_loop()`: 30초마다 heartbeat (running_agents 목록)
  - `_handle(msg)`: spawn_agent / kill_agent / drain / ping 처리
  - **WebSocket 인증**: `Sec-WebSocket-Protocol: anygarden.v1, bearer.<machine_token>` (쿼리 파라미터 금지)
- [ ] **검증**: `pytest tests/test_daemon.py` — mock WS 서버와 register/heartbeat 5개 테스트

### Phase 4D: Spawner + Supervisor (Day 3)

- [ ] `anygarden_machine/spawner.py` (§10.6 코드 기반):
  - `spawn(msg) -> SpawnResult`: 
    - profile_yaml을 `/tmp/anygarden-agent-{id}.yaml`로 저장 (chmod 600)
    - 토큰은 **환경변수 `ANYGARDEN_TOKEN`으로만** 전달 (argv 금지, ps aux 노출 방지)
    - `asyncio.create_subprocess_exec(["uvx", "--from", "anygarden-sdk[{engine}]", "anygarden-agent", ...])`
    - 자식 프로세스 종료 감시 태스크 `_watch()` 시작
  - `kill(agent_id) -> dict`: SIGTERM → 10초 대기 → SIGKILL
  - `list_running() -> list[dict]`: heartbeat에 포함할 running agent 목록
  - `_cleanup(agent_id)`: 프로필 임시 파일 삭제 + 내부 상태 정리
- [ ] `anygarden_machine/supervisor.py`:
  - `_watch(agent_id, proc)`: `proc.wait()` → exit code 확인 → 정상(0) vs 크래시(nonzero)
  - 크래시 시 stderr tail 수집 (최대 2KB) → daemon에 `agent_crashed` 이벤트 전달
- [ ] **검증**: `pytest tests/test_spawner.py` — mock subprocess, spawn/kill/watch 6개 테스트

### Phase 4E: CLI (Day 4)

- [ ] `anygarden_machine/cli.py`:
  - `anygarden-machine register --server URL --name NAME`:
    1. 유저 인증 (email/password → JWT 획득)
    2. `detect_engines()` 실행
    3. `POST /api/v1/machines` → machine_id + machine_token 수신
    4. `~/.anygarden/machine.toml` + `~/.anygarden/machine.token` 저장
  - `anygarden-machine run`:
    1. config 로드
    2. `MachineDaemon(...)` 생성 + `run()` 실행
  - `anygarden-machine status`:
    - config에서 machine_id/server_url 읽기
    - `GET /api/v1/machines/{id}` → 상태 출력
    - 로컬 spawner의 running_agents 목록
  - `anygarden-machine install-systemd-unit`:
    - `~/.config/systemd/user/anygarden-machine.service` 파일 생성 (§10.10 정본)
    - `systemctl --user daemon-reload` 안내
- [ ] **검증**: `anygarden-machine --help` 출력, mock 기반 register 테스트

### Phase 4F: 통합 검증 (Day 5)

- [ ] **서버에 임시 `/ws/machines/{id}` 핸들러 추가** (Week 5에서 본격 구현, 여기서는 echo 수준):
  - WebSocket accept + register 프레임 수신 → `machines.status='online'` DB 업데이트
  - spawn_agent 프레임 수동 전송 → Daemon이 실제 subprocess spawn 확인
- [ ] 수동 E2E:
  1. `uvx anygarden-server` 실행
  2. `uvx anygarden-machine register --server http://localhost:8000 --name test-box`
  3. `uvx anygarden-machine run` → "Connected, waiting for spawn commands"
  4. 서버에서 수동으로 spawn_agent 프레임 전송 → Daemon이 agent subprocess 띄움
  5. Agent가 `/ws/rooms/X`에 접속하여 채팅 참여 확인
- [ ] `ruff check anygarden_machine tests` + `mypy anygarden_machine`

---

## 4. 테스트 전략

| 범주 | 파일 | 수 | 시나리오 |
|------|------|---|---------|
| 단위 | `test_detector.py` | 4 | binary 감지, python import, env 감지, 타임아웃 |
| 단위 | `test_spawner.py` | 6 | spawn, kill, watch, cleanup, env 토큰, profile chmod |
| 통합 | `test_daemon.py` | 5 | WS 연결, register, heartbeat, spawn 수신, 재연결 |
| **합계** | | **15** | 누적 87개 (W1-3 72개 + W4 15개) |

---

## 5. 보안 체크리스트 (§10.12 준수)

- [ ] Machine Token은 `~/.anygarden/machine.token` 파일로 분리 저장 (config.toml에 넣지 않음)
- [ ] `machine.token` 파일 chmod 600
- [ ] WebSocket 인증은 `Sec-WebSocket-Protocol: anygarden.v1, bearer.<token>` (쿼리 파라미터 금지)
- [ ] Agent subprocess에 토큰은 환경변수 `ANYGARDEN_TOKEN`으로만 전달 (argv 금지)
- [ ] 프로필 임시 파일 chmod 600 + 종료 시 자동 삭제
- [ ] Daemon이 채팅 메시지를 볼 수 있는 경로 없음 (제어 평면만 사용)
- [ ] **Daemon 침해 시 spawn한 Agent의 토큰이 노출되는 구조적 한계를 문서화** (§10.12.4)

---

## 6. 완료 기준

- [ ] `uvx anygarden-machine register --server http://localhost:8000` 동작
- [ ] `uvx anygarden-machine run` 실행 시 서버에 "online" 상태로 등록
- [ ] `uvx anygarden-machine status` 출력
- [ ] 서버에서 spawn_agent 프레임 전송 시 Daemon이 실제 subprocess 생성
- [ ] Agent subprocess가 서버에 `/ws/rooms/{id}`로 독립 접속 성공
- [ ] Agent subprocess 강제 kill 시 Daemon이 `agent_crashed` 보고
- [ ] 15개 테스트 통과
- [ ] `ruff check` + `mypy` 에러 0

---

## 7. 참고

- [10-machine-scheduler.md](../10-machine-scheduler.md) §10.3-§10.6 (패키지 구조, 코드)
- [10-machine-scheduler.md](../10-machine-scheduler.md) §10.8 (프로토콜 프레임)
- [10-machine-scheduler.md](../10-machine-scheduler.md) §10.9 (CLI)
- [10-machine-scheduler.md](../10-machine-scheduler.md) §10.10 (systemd unit)
- [10-machine-scheduler.md](../10-machine-scheduler.md) §10.12 (보안)

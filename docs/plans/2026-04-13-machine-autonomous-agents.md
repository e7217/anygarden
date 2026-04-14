---
title: Machine Autonomous Agent Management — Declarative Desired-State Model
date: 2026-04-13
status: design
---

# Machine Autonomous Agent Management

## 배경

현재 에이전트 라이프사이클은 **명령형(imperative) 모델**로, doorae-server가 모든 결정을 내리고 doorae-machine은 수동적 실행기(command executor)이다.

```
Server: "spawn_agent" → Machine: 실행
Server: "kill_agent"  → Machine: 정지
```

이로 인한 문제:

1. **서버 재시작 시 에이전트 전원 중단** — `app.py` lifespan이 모든 running/starting 에이전트를 `pending`으로 리셋하고 `placed_on_machine_id=None`으로 초기화. 머신에서 멀쩡히 돌고 있어도 서버가 "처음부터 다시" 함.
2. **머신(데몬) 재시작 시 에이전트 소멸** — `Spawner._agents`가 in-memory dict이라 전부 사라짐. 서버의 reverse reconcile이 `pending`으로 떨어뜨리고 재스케줄하지만, 서버가 없으면 복구 불가.
3. **네트워크 단절 중 crash 시 복구 불가** — 머신이 서버에 `agent_crashed`를 보고할 수 없으면 재시작 판단이 이뤄지지 않아 에이전트가 죽은 채 방치됨.

## 목표

1. **서버 무중단 배포** — 서버 재시작 시 머신의 에이전트는 영향 없이 계속 실행
2. **머신 자력 복구** — 데몬 재시작 시 로컬 manifest에서 에이전트를 자력으로 재시작
3. **네트워크 단절 내성** — 서버 연결이 끊겨도 crash된 에이전트를 머신이 로컬에서 재시작
4. **desired/actual 분리** — 서버는 의도(desired state)를 소유하고, 머신은 실행(actual state)을 소유
5. **config 즉시 반영** — 서버에서 에이전트 config 변경 시 `generation` 증가를 통해 머신이 자동 재시작

비목표:
- 머신간 에이전트 마이그레이션 (머신 자발적 이전) — 추후 단계
- 에이전트의 서버 독립 동작 (오프라인 로컬 작업) — 에이전트는 채팅이 목적이므로 서버 필수

## 설계 결정 (brainstorming 합의)

| 항목 | 결정 | 근거 |
|------|------|------|
| 범위 | Full Declarative desired-state sync | 프로토콜 수준 전환으로 근본 해결 |
| 호환성 | Breaking change, 동시 전환 | 개발 단계이므로 호환 레이어 불필요 |
| Manifest 영속화 | 토큰 제외, 서버에서 재발급 | 에이전트는 서버 없이 기능 못 함 → 토큰 디스크 저장의 보안 리스크 대비 실익 ~1초 |
| 동기화 방향 | 양방향 diff sync | 재연결 시 한 번의 왕복으로 완전 동기화 |
| Crash restart | 머신 로컬 재시작 + 횟수 제한 | 서버 왕복 제거 + crash loop 방지 |
| Config 변경 반영 | 즉시 sync → generation 비교 → 에이전트 재시작 | hot reload 복잡도 없이 일관성 보장 |
| 구현 순서 | Bottom-up: 프로토콜 정의 → 머신 → 서버 | 머신 로컬 테스트 가능 + 가장 복잡한 서버를 마지막에 집중 |

## 아키텍처

### 현재 (명령형)

```
Server (모든 결정)              Machine (수동 실행기)
┌─────────────────────┐        ┌──────────────────────┐
│ desired + actual     │        │ _agents: dict (메모리)│
│ state 모두 소유      │ spawn  │ spawn/kill만 수행     │
│ restart policy 판단  │──────→ │ heartbeat로 보고      │
│ placement 결정       │ kill   │ 자체 판단 없음        │
└─────────────────────┘──────→ └──────────────────────┘
```

### 목표 (선언형)

```
Server (Desired State)          Machine (Actual State)
┌─────────────────────┐        ┌──────────────────────────┐
│ desired_state 결정   │  sync  │ 프로세스 lifecycle 소유    │
│ placement 결정       │◄─────►│ 로컬 manifest 영속화      │
│ generation 관리      │  diff  │ crash restart 로컬 실행   │
│ 전체 상태 조회       │        │ actual_state 보고         │
└─────────────────────┘        └──────────────────────────┘
```

## WS 프로토콜

### 제거되는 프레임

| 프레임 | 방향 | 대체 |
|--------|------|------|
| `spawn_agent` | Server → Machine | `sync_desired_state` |
| `kill_agent` | Server → Machine | `sync_desired_state` (desired_state="stopped") |
| `agent_started` | Machine → Server | `report_actual_state` |
| `agent_crashed` | Machine → Server | `report_actual_state` |
| `agent_stopped` | Machine → Server | `report_actual_state` |
| `heartbeat` | Machine → Server | `report_actual_state` (주기적) |

### 유지되는 프레임

| 프레임 | 방향 | 용도 |
|--------|------|------|
| `register` | Machine → Server | 최초 연결 시 머신 등록 |
| `rotate_token` | Server → Machine | 머신 토큰 로테이션 |

### 신규 프레임

#### Server → Machine

**`sync_desired_state`** — 에이전트 하나의 desired state 전달

```python
class SyncDesiredStateFrame(BaseModel):
    type: Literal["sync_desired_state"] = "sync_desired_state"
    agent_id: str
    desired_state: Literal["running", "stopped"]
    generation: int  # config 버전, 변경마다 +1

    # spawn payload (desired_state="running"일 때만 유의미)
    engine: str = ""
    name: str = ""
    profile_yaml: str = ""
    rooms: list[str] = []
    agents_md: str | None = None
    files: dict[str, str] = {}
    engine_secrets: dict[str, str] = {}
    reasoning_effort: str | None = None
    sub_rooms: list[dict] = []

    # restart policy (머신이 로컬에서 적용)
    restart_policy: Literal["stop", "restart_on_same_machine", "restart_anywhere"] = "restart_anywhere"
    max_restarts: int = 3
    restart_window_seconds: int = 300
```

**`sync_batch`** — 재연결 시 일괄 전송

```python
class SyncBatchFrame(BaseModel):
    type: Literal["sync_batch"] = "sync_batch"
    agents: list[SyncDesiredStateFrame]
```

**`token_grant`** — 토큰 재발급 응답

```python
class TokenGrantFrame(BaseModel):
    type: Literal["token_grant"] = "token_grant"
    agent_id: str
    agent_token: str
```

#### Machine → Server

**`report_actual_state`** — actual state 보고 (heartbeat 대체)

```python
class AgentActual(BaseModel):
    agent_id: str
    actual_state: Literal["running", "stopped", "crashed", "starting"]
    pid: int | None = None
    engine: str = ""
    generation: int = 0
    uptime_seconds: int = 0
    last_crash_reason: str | None = None

class ReportActualStateFrame(BaseModel):
    type: Literal["report_actual_state"] = "report_actual_state"
    agents: list[AgentActual]
```

**`token_request`** — 토큰 재발급 요청

```python
class TokenRequestFrame(BaseModel):
    type: Literal["token_request"] = "token_request"
    agent_ids: list[str]
```

**`request_replacement`** — 재배치 요청

```python
class RequestReplacementFrame(BaseModel):
    type: Literal["request_replacement"] = "request_replacement"
    agent_id: str
    reason: str  # "crash_budget_exhausted"
```

### 재연결 시퀀스

```
Machine reconnects
  1. Machine → Server: register (기존)
  2. Machine → Server: report_actual_state (현재 돌고 있는 에이전트들)
  3. Server → Machine: sync_batch (이 머신에 배치된 모든 에이전트의 desired state)
  4. Machine: diff reconcile 수행
     - desired=running, actual=없음 → token_request → token_grant → spawn
     - desired=running, actual=running, generation 동일 → no-op
     - desired=running, actual=running, generation 다름 → kill → token_request → respawn
     - desired=stopped, actual=running → kill
     - desired=없음 (sync_batch에 없는), actual=running → orphan → kill
```

### 정상 운영 시퀀스

```
# 관리자가 에이전트 시작 요청
Admin → Server: POST /api/v1/agents/{id}/start
Server: desired_state="running", generation++ 저장
Server → Machine: sync_desired_state (desired="running", generation=N)
Machine: manifest 저장 → token_request
Server → Machine: token_grant
Machine: spawn → report_actual_state (actual="running")
Server: DB 갱신 (actual_state="running")

# 관리자가 에이전트 정지 요청
Admin → Server: POST /api/v1/agents/{id}/stop
Server: desired_state="stopped" 저장
Server → Machine: sync_desired_state (desired="stopped")
Machine: kill → manifest의 desired_state 갱신 → report_actual_state (actual="stopped")
Server: DB 갱신 (actual_state="stopped")

# 에이전트 crash (서버 연결 정상)
Machine: 에이전트 프로세스 crash 감지
Machine: restart_policy + crash budget 확인 → 재시작 결정
Machine: respawn (로컬 manifest + 기존 토큰) → report_actual_state (actual="running", crash 이력 포함)
Server: DB 갱신

# 에이전트 crash (서버 연결 끊김)
Machine: 에이전트 프로세스 crash 감지
Machine: restart_policy + crash budget 확인 → 재시작 결정
Machine: respawn (로컬 manifest + 기존 토큰)
  → 에이전트가 서버 WS 연결 실패 → 재연결 루프
  → 서버 복구 시 에이전트 자동 연결
Machine: 서버 재연결 시 report_actual_state로 동기화

# crash budget 초과
Machine: max_restarts 횟수 도달
Machine: restart_policy에 따라 분기
  - "stop" 또는 "restart_on_same_machine": 에이전트 정지, report_actual_state
  - "restart_anywhere": request_replacement → 서버가 다른 머신에 재배치
```

## doorae-machine 변경

### ManifestStore — 로컬 manifest 영속화

```
~/.doorae/agents/<agent_id>/
├── manifest.json         # SyncDesiredState에서 토큰 제외한 영속 데이터
├── AGENTS.md             # (기존) 에이전트 지시 문서
├── CLAUDE.md → AGENTS.md # (기존) 심볼릭 링크
├── skills/               # (기존) 스킬 파일
├── workspace/            # (기존) 런타임 스크래치
│   └── MEMORY.md
└── .claude/, .agents/    # (기존) 엔진별 심볼릭 링크
```

`manifest.json` 구조:

```json
{
  "agent_id": "abc123",
  "desired_state": "running",
  "generation": 5,
  "engine": "claude-code",
  "name": "code-reviewer",
  "profile_yaml": "...",
  "rooms": ["room-1", "room-2"],
  "agents_md": "...",
  "files": {"skills/review/SKILL.md": "..."},
  "reasoning_effort": "medium",
  "sub_rooms": [{"name": "sub-1", "description": "..."}],
  "restart_policy": "restart_anywhere",
  "max_restarts": 3,
  "restart_window_seconds": 300,
  "saved_at": "2026-04-13T12:00:00Z"
}
```

`engine_secrets`와 `agent_token`은 manifest.json에 저장하지 않는다.

### CrashBudget — 로컬 재시작 횟수 제한

```python
@dataclass
class CrashBudget:
    max_restarts: int = 3
    window_seconds: int = 300
    timestamps: list[float] = field(default_factory=list)

    def record_crash(self) -> bool:
        """crash 기록 후 재시작 가능 여부 반환."""
        now = time.time()
        cutoff = now - self.window_seconds
        self.timestamps = [t for t in self.timestamps if t > cutoff]
        self.timestamps.append(now)
        return len(self.timestamps) <= self.max_restarts
```

- crash 발생 시 `CrashBudget.record_crash()` 호출
- `True` 반환 → 로컬 재시작 (토큰이 유효한 동안 기존 토큰 재사용)
- `False` 반환 → restart_policy에 따라:
  - `stop`: 정지, `report_actual_state`로 보고
  - `restart_on_same_machine`: 정지, `report_actual_state`로 보고
  - `restart_anywhere`: `request_replacement` 전송

### Spawner 변경

- `_agents: dict[str, RunningAgent]` → 여전히 in-memory (프로세스 핸들)
- 데몬 시작 시 `ManifestStore.load_all()` → desired_state="running"인 에이전트 목록 획득
- 서버 연결 시 `token_request` → `token_grant` 수신 후 spawn
- 서버 연결 전에는 spawn하지 않음 (토큰 미보유)
- crash 콜백: 서버에 보고 대신 `CrashBudget` 확인 → 로컬 재시작 또는 정지 결정

### 데몬 프레임 핸들러 변경

```python
# 기존
"spawn_agent"  → spawner.spawn(msg)
"kill_agent"   → spawner.kill(agent_id)

# 변경
"sync_desired_state" → manifest_store.save(msg) → reconcile_one(agent_id)
"sync_batch"         → manifest_store.save_batch(msgs) → reconcile_all()
"token_grant"        → pending_token_requests[agent_id].set_result(token)
```

## doorae-server 변경

### lifecycle.py — 선언형 전환

```python
# 기존: request_start() → select_machine → spawn_agent 프레임 전송
# 변경: request_start() → select_machine → sync_desired_state 프레임 전송
#       토큰 발급은 머신의 token_request 수신 시 수행

# 기존: on_agent_crashed() → restart_policy 판단 → request_start()
# 변경: on_agent_crashed() 제거 — 머신이 로컬에서 처리
#       request_replacement 수신 시만 서버가 재배치 판단

# 기존: request_stop() → kill_agent 프레임 전송
# 변경: request_stop() → sync_desired_state(desired="stopped") 프레임 전송
```

주요 메서드 변경:

| 기존 메서드 | 변경 |
|------------|------|
| `request_start()` | placement + `sync_desired_state` 전송, 토큰 미포함 |
| `request_stop()` | `sync_desired_state(desired="stopped")` 전송 |
| `on_agent_started()` | `_handle_report_actual_state()`로 통합 |
| `on_agent_crashed()` | `_handle_report_actual_state()`로 통합 (서버는 DB 기록만) |
| `on_agent_stopped()` | `_handle_report_actual_state()`로 통합 |
| (신규) | `handle_token_request()` — 토큰 발급 + `token_grant` 응답 |
| (신규) | `handle_request_replacement()` — 재배치 결정 |

### app.py lifespan — 강제 리셋 제거

```python
# 기존 (제거):
await db.execute(
    update(_Agent)
    .where(_Agent.actual_state.in_(["running", "starting"]))
    .values(actual_state="pending", pid=None, placed_on_machine_id=None)
)

# 변경: 서버 시작 시 에이전트 상태를 건드리지 않음.
# 머신이 재연결하면 report_actual_state → sync_batch → diff reconcile로 수렴.
# 머신이 재연결하지 않는 경우(머신 장애)를 위한 타임아웃:
#   - daemon_last_seen_at + 5분 초과 → actual_state를 "unknown"으로 마킹
#   - 별도 background task가 주기적으로 확인
```

### machine_handler.py — reconcile 재설계

```python
# 기존: _handle_heartbeat() — forward/reverse reconcile
# 변경: _handle_report_actual_state()

async def _handle_report_actual_state(machine_id, data, lifecycle):
    """actual state 보고 수신 → DB 갱신 + sync_batch 응답."""
    reported = data["agents"]

    async with session_factory() as db:
        for agent_actual in reported:
            agent = await get_agent(db, agent_actual["agent_id"])
            if agent is None:
                continue  # orphan — sync_batch에 미포함 → 머신이 kill
            agent.actual_state = agent_actual["actual_state"]
            agent.pid = agent_actual.get("pid")
            if agent_actual.get("last_crash_reason"):
                agent.last_crash_reason = agent_actual["last_crash_reason"]
        await db.commit()

    # 이 머신에 배치된 모든 에이전트의 desired state를 sync_batch로 응답
    await send_sync_batch(machine_id)
```

### Agent 모델 — generation 필드 추가

```python
class Agent(Base):
    # ... 기존 필드
    generation: int = 0  # config 변경마다 +1
```

에이전트의 config 관련 필드가 변경될 때 `generation += 1`:
- `agents_md`, `profile_yaml`, `engine`, `rooms`, `reasoning_effort`, `agent_files`

## 장애 시나리오별 동작

### 서버 재시작

```
Before: 서버 시작 → 모든 에이전트 pending 리셋 → 머신 재연결 → 재스케줄 → 재spawn
After:  서버 시작 → (상태 안 건드림) → 머신 재연결 → report_actual_state
        → sync_batch → diff = 모두 일치 → no-op. 에이전트 무중단.
```

### 데몬 재시작

```
Before: 프로세스 전부 소멸 → 서버 reconcile → pending → 재spawn
After:  프로세스 전부 소멸 → 데몬 시작 → manifest.json 로드
        → 서버 연결 → token_request → token_grant → 로컬 spawn
        → report_actual_state로 서버 동기화
```

### 네트워크 단절 중 crash

```
Before: crash → agent_crashed 전송 실패 → 에이전트 죽은 채 방치
After:  crash → CrashBudget 확인 → 로컬 재시작 (기존 토큰 재사용)
        → 에이전트가 서버 WS 재연결 시도 (서버 복구 시 자동 연결)
        → 데몬이 서버 재연결 시 report_actual_state로 동기화
```

### 서버 다운 + 데몬 재시작

```
Before: 서버 없으면 에이전트 복구 불가
After:  데몬 시작 → manifest.json 로드 → 서버 연결 시도 실패
        → 토큰 없으므로 spawn 불가 → 서버 복구 대기
        → 서버 복구 시 token_request → spawn
        (에이전트는 어차피 서버 없이 채팅 불가이므로 실효적 차이 없음)
```

## 구현 순서

### Phase 1: 프로토콜 프레임 정의

양쪽(doorae-machine, doorae-server) 공통 프레임 모델 정의.
doorae-machine의 `protocol/frames.py`와 doorae-server의 WS 핸들러에 새 프레임 추가.

### Phase 2: doorae-machine 변경

1. `ManifestStore` — manifest.json 읽기/쓰기/목록
2. `CrashBudget` — 로컬 재시작 횟수 제한
3. `Spawner` 변경 — manifest 기반 spawn, crash 로컬 처리
4. 데몬 프레임 핸들러 — `sync_desired_state`, `sync_batch`, `token_grant` 처리
5. 재연결 로직 — `report_actual_state` + `token_request` 시퀀스

### Phase 3: doorae-server 변경

1. Agent 모델에 `generation` 필드 추가 (마이그레이션)
2. `lifecycle.py` 선언형 전환
3. `machine_handler.py` — `_handle_report_actual_state`, `_handle_token_request`, `_handle_request_replacement`
4. `app.py` lifespan — 강제 리셋 제거, stale machine 타임아웃 task 추가
5. config 변경 시 generation 증가 + sync_desired_state 자동 push

### Phase 4: 통합 테스트

1. 서버 재시작 시 에이전트 무중단 확인
2. 데몬 재시작 시 자력 복구 확인
3. crash → 로컬 재시작 → crash budget 초과 → request_replacement 확인
4. config 변경 → generation 증가 → 에이전트 자동 재시작 확인
5. orphan agent 정리 확인

## Open Questions

1. **stale machine 타임아웃**: 머신이 재연결하지 않는 경우 얼마나 기다린 후 해당 머신의 에이전트를 재배치할 것인가? (제안: 5분)
2. **token 유효기간**: crash 후 로컬 재시작 시 기존 토큰을 재사용하는데, 토큰 만료 정책이 필요한가? (현재 토큰은 만료 없음)
3. **concurrent sync**: 서버가 sync_desired_state를 보내는 동안 머신이 report_actual_state를 보내면 race condition이 있는가? (generation 번호로 해결 가능)

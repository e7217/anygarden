# Week 5: 서버 스케줄러 + E2E 테스트 + v0.2.0

> **목표**: `POST /api/v1/agents` → 스케줄러 → Machine Daemon 자동 spawn 전체 흐름 동작
> **산출물**: 서버 스케줄러 모듈, Machine WS 핸들러, 선언적 에이전트 생성 REST API, v0.2.0 릴리즈
> **정본 참조**: [10-machine-scheduler.md](../10-machine-scheduler.md) §10.7-§10.8, §10.11-§10.13

---

## 1. 요약

Week 4에서 Machine Daemon이 서버에 연결하여 spawn 명령을 받을 수 있게 되었다. Week 5에서 **서버 쪽 스케줄러**를 구현하여 전체 흐름을 완성한다:

1. Machine 등록 REST API (`POST /api/v1/machines`)
2. Machine WebSocket 핸들러 (`/ws/machines/{id}`)
3. 에이전트 선언적 생성 REST API (`POST /api/v1/agents`)
4. 스케줄러: bin-pack placement + 생명주기 상태 머신 + MachineBus
5. Machine Token 인증 (`auth/machine_token.py`)
6. DB 마이그레이션 (Machine/Agent 테이블 확장)
7. E2E 테스트: register → run → POST /agents → spawn → 크래시 복구
8. 부하 테스트 (Locust)
9. v0.2.0 릴리즈

---

## 2. 추가할 서버 파일

```
doorae-server/doorae/
├── auth/
│   └── machine_token.py             # [60 LOC] Machine Token 발급/검증
├── scheduler/
│   ├── __init__.py
│   ├── placement.py                 # [80 LOC] bin-pack Machine 선택
│   ├── lifecycle.py                 # [100 LOC] Agent 상태 머신
│   └── machine_bus.py               # [60 LOC] 활성 Machine WS 연결 풀
├── ws/
│   └── machine_handler.py           # [120 LOC] /ws/machines/{id} 핸들러
├── api/v1/
│   ├── machines.py                  # [40 LOC] POST /api/v1/machines (등록)
│   └── agents.py                    # [40 LOC] POST /api/v1/agents (선언적 생성)
└── db/migrations/versions/
    └── 002_machine_scheduling.py    # Machine/Agent 테이블 확장
```

**추가 LOC**: ~500. 서버 합계 ~1,040 (Week 1-2) + ~500 = **~1,540** (목표 ~1,330-1,750 범위 내).

---

## 3. 구현 단계

### Phase 5A: DB 마이그레이션 (Day 1 오전)

- [ ] `002_machine_scheduling.py` Alembic 마이그레이션:
  - `machines` 테이블: owner_user_id, status, daemon_last_seen_at, daemon_version, cpu_cores, memory_gb, max_agents, labels(JSON) 컬럼 추가
  - `machine_engines` 테이블 생성 (machine_id, engine, version)
  - `machine_tokens` 테이블 생성 (id, machine_id, token_hash, lookup_hint, created_at, expires_at, revoked_at)
  - `agents` 테이블: engine, placed_on_machine_id, desired_state, actual_state, pid, profile_yaml, started_at, last_heartbeat_at, last_crash_reason, restart_policy 컬럼 추가
  - 인덱스: `ix_machines_status_owner`, `ix_machine_engines_engine`, `ix_machine_tokens_hint`, `ix_agents_placed_state`
- [ ] `doorae/db/models.py`에 `MachineEngine`, `MachineToken` 모델 추가 + `Machine`, `Agent` 모델 확장
- [ ] **검증**: `alembic upgrade head` 성공 + 스키마 검증

### Phase 5B: Machine Token 인증 (Day 1 오후)

- [ ] `doorae/auth/machine_token.py`:
  - `generate_machine_token() -> str` (token_urlsafe(32))
  - `verify_machine_token(raw_token, db) -> MachineToken | None` (argon2 + lookup_hint)
- [ ] `doorae/auth/dependencies.py`에 `get_machine_identity()` 추가:
  - `Sec-WebSocket-Protocol: doorae.v1, bearer.<machine_token>` 파싱
  - Machine Token 검증 → `MachineIdentity(machine_id)` 반환
- [ ] **검증**: Machine Token 발급/검증 단위 테스트 5개

### Phase 5C: Machine 등록 REST API (Day 2 오전)

- [ ] `doorae/api/v1/machines.py`:
  - `POST /api/v1/machines {name, labels}` → machine_id + machine_token 반환
  - 인증: User JWT 필요 (owner)
  - Machine 생성 + MachineToken 발급 (argon2 해시 저장, 평문 1회 반환)
  - `GET /api/v1/machines` → Machine 목록 (admin)
  - `POST /api/v1/machines/{id}/drain` → draining 상태 전환
  - `POST /api/v1/machines/{id}/tokens/revoke` → 토큰 즉시 무효화
- [ ] **검증**: REST API 테스트 5개

### Phase 5D: Machine WebSocket 핸들러 (Day 2 오후)

- [ ] `doorae/scheduler/machine_bus.py`:
  - `MachineBus.__init__()` — `dict[UUID, WebSocket]` 인메모리 풀
  - `register(machine_id, ws)` / `unregister(machine_id)`
  - `is_connected(machine_id) -> bool`
  - `send(machine_id, frame) -> bool`
- [ ] `doorae/ws/machine_handler.py` (§10.8.3 코드 기반):
  - `@router.websocket("/ws/machines/{machine_id}")`
  - `Sec-WebSocket-Protocol` subprotocol 인증 → `accept(subprotocol="doorae.v1")`
  - `register` 프레임 수신 → capabilities를 `machine_engines` 테이블에 저장 + `status='online'`
  - `heartbeat` 프레임 → `daemon_last_seen_at` 갱신
  - `agent_started` / `agent_crashed` / `agent_stopped` → lifecycle 호출
  - 연결 끊김 → `status='offline'`
- [ ] **검증**: mock WS 클라이언트로 register/heartbeat/연결 끊김 테스트 5개

### Phase 5E: 스케줄러 (Day 3)

- [ ] `doorae/scheduler/placement.py` (§10.7.1 코드 기반):
  - `select_machine_for(engine, db, machine_bus, required_labels) -> Machine`
  - 필터: status='online' + engine 매칭 + 활성 연결 + max_agents 미초과 + labels
  - 선택: bin-pack (가장 적은 running agent)
  - 실패 시: `NoSuitableMachineError`
- [ ] `doorae/scheduler/lifecycle.py` (§10.7.2 코드 기반):
  - `AgentLifecycle(db, machine_bus)`
  - `request_start(agent_id)`: Machine 선택 → agent_token 발급 → spawn_agent 전송
  - `on_agent_started(agent_id, pid)`: actual_state='running'
  - `on_agent_crashed(agent_id, exit_code, stderr_tail)`: restart_policy 적용
  - `request_stop(agent_id)`: kill_agent 전송
  - 상태 머신: pending → starting → running → crashed / stopping → stopped
- [ ] **검증**: placement 테스트 5개 (bin-pack, 필터, 용량 초과) + lifecycle 테스트 5개 (상태 전이)

### Phase 5F: 선언적 에이전트 생성 REST API (Day 4 오전)

- [ ] `doorae/api/v1/agents.py`:
  - `POST /api/v1/agents {engine, profile, name, rooms, placement}`:
    1. Agent 레코드 생성 (desired_state='running', actual_state='pending')
    2. `lifecycle.request_start(agent_id)` 호출 → 스케줄러가 Machine 선택 + spawn
    3. 201 `{agent_id, actual_state:'pending'}` 반환
  - `GET /api/v1/agents` → Agent 목록
  - `DELETE /api/v1/agents/{id}` → `lifecycle.request_stop(agent_id)`
- [ ] **검증**: 선언적 생성 → 실제 spawn 통합 테스트

### Phase 5G: E2E 테스트 + 부하 테스트 (Day 4 오후 ~ Day 5)

- [ ] E2E 테스트 시나리오 (실제 서버 + Machine Daemon + Agent):
  1. **기본 흐름**: Machine register → Daemon run → POST /agents → Agent spawn → Room 참여 → 메시지 교환
  2. **크래시 복구**: Agent 강제 kill → Daemon이 `agent_crashed` 보고 → 서버가 `restart_on_same_machine` 정책 적용 → 재시작
  3. **Machine drain**: POST /machines/{id}/drain → 기존 Agent 유지 + 새 spawn 거부
  4. **재연결**: 서버 재시작 → Daemon 재연결 + 재등록 → Agent 상태 복구

- [ ] Locust 부하 테스트:
  - 50 WebSocket 연결 동시
  - 10 msg/s 처리량
  - p99 fanout <50ms 목표

- [ ] **검증**: E2E 4개 시나리오 통과 + SLO 달성

### Phase 5H: Prometheus 지표 추가 + v0.2.0 릴리즈 (Day 5)

- [ ] `doorae/observability/metrics.py`에 Machine 지표 2개 추가:
  - `doorae_machines_online` (Gauge)
  - `doorae_agents_by_state{state}` (Gauge)
- [ ] doorae-server `__version__ = "0.2.0"`, doorae-machine `__version__ = "0.1.0"`
- [ ] CHANGELOG 갱신
- [ ] v0.2.0 태그 + PyPI 배포 (doorae-server + doorae-machine)
- [ ] README 빠른 시작 갱신 (Scheduled 모드 예시 포함)

---

## 4. 테스트 전략

| 범주 | 파일/방법 | 수 | 시나리오 |
|------|---------|---|---------|
| 단위 | `test_machine_token.py` | 5 | 발급/검증/만료/revoke/lookup_hint |
| 단위 | `test_placement.py` | 5 | bin-pack, engine 필터, labels, 용량 초과, 연결 없음 |
| 단위 | `test_lifecycle.py` | 5 | 상태 전이 5단계, restart_policy 3종 |
| 통합 | `test_machine_handler.py` | 5 | register, heartbeat, spawn 명령, 연결 끊김, drain |
| 통합 | `test_agents_api.py` | 3 | 선언적 생성, 상태 조회, 삭제 |
| 통합 | `test_machines_api.py` | 5 | 등록, 목록, drain, revoke, 이중 등록 |
| E2E | 수동 + 스크립트 | 4 | 기본 흐름, 크래시 복구, drain, 재연결 |
| 부하 | Locust | 1 | 50 연결 / 10 msg/s / p99 <50ms |
| **합계** | | **33** | 누적 120개 (W1-4 87개 + W5 33개) |

---

## 5. 완료 기준 (v0.2.0)

- [ ] `POST /api/v1/machines` → Machine 등록 + machine_token 발급 동작
- [ ] `doorae-machine register + run` → 서버에 online 상태 표시
- [ ] `POST /api/v1/agents {engine, profile}` → 스케줄러가 Machine 선택 → Daemon이 자동 spawn
- [ ] Agent subprocess가 서버에 독립 WebSocket 접속하여 채팅 참여
- [ ] Agent 강제 kill 시 `restart_on_same_machine` 정책 자동 재시작
- [ ] Machine drain 시 새 spawn 거부 + 기존 Agent 유지
- [ ] E2E 4개 시나리오 통과
- [ ] Locust: 50 연결 / 10 msg/s / p99 <50ms
- [ ] 120개 테스트 통과
- [ ] v0.2.0 태그 + PyPI 배포 (doorae-server v0.2.0 + doorae-machine v0.1.0)

---

## 6. v0.2.0의 한계 (v1.0까지 남은 것)

| 미포함 | 예정 시점 |
|--------|---------|
| TypeScript SDK (`@doorae/sdk`) | Phase 2 (Week 6-7) |
| PyInstaller 바이너리 배포 | Phase 3 (Week 8+) |
| `get.doorae.io` 설치 스크립트 | Phase 3 |
| `reschedule` 정책 (다른 Machine으로 재배치) | v0.3.0 |
| Federation (다중 인스턴스 연동) | Plan B/C 영역 |
| Event Store (감사 추적) | Plan B 영역 |

---

## 7. 참고

- [10-machine-scheduler.md](../10-machine-scheduler.md) §10.7 스케줄러 (placement, lifecycle, machine_bus)
- [10-machine-scheduler.md](../10-machine-scheduler.md) §10.8 WebSocket 프로토콜 프레임
- [10-machine-scheduler.md](../10-machine-scheduler.md) §10.11 실패 시나리오
- [10-machine-scheduler.md](../10-machine-scheduler.md) §10.12 보안 (Machine Token, Daemon 침해 4단계 분류)
- [10-machine-scheduler.md](../10-machine-scheduler.md) §10.13 구현 체크리스트
- [08-operations.md](../08-operations.md) §8.8 로드맵, §8.9 완료 체크리스트

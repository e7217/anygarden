# feat(reliability): Wave 1a — ActivityLog outcome/engine columns + agent heartbeat reaper (#447)

- Commit: `8547fd3` (8547fd32a1333d5d4ee2deacfa954364edc6b066)
- Author: Changyong Um
- Date: 2026-06-18T21:09:03+09:00
- PR: #447

## Situation

ADR-006(신뢰성 하드닝 전략)의 Wave 1 중 위험도가 가장 낮은 조각. 8개 영역 감사에서 두 가지 "관측/탐지" 갭이 확인됐다: (1) turn 결과(`outcome`)·`engine`이 ActivityLog의 `details` JSON 안에만 있어 "지난 1시간 실패 turn" 류 운영 쿼리가 full-scan + json_extract을 강요했고, (2) `last_heartbeat_at`이 어디서도 임계 비교되지 않아 전원이 끊긴 머신의 에이전트가 영원히 `running`으로 남아 bin-pack 배치를 오염시켰다.

## Task

한 PR로, 추가적(additive)·결정론적이며 스위치보드 철학을 깨지 않는 범위에서:

- ActivityLog `outcome`/`engine`을 1급 인덱스 컬럼으로 승격(#427 room_id 선례) + 마이그레이션 + 조회 필터
- 에이전트 heartbeat reaper로 stale running 에이전트를 `crashed`로 — 단, spawn 중 `starting` 에이전트 오탐 금지
- 머신 staleness sweep은 제외(`daemon_last_seen_at` 미갱신으로 살아있는 함대 오프라인 처리 위험 — 선행작업 필요)

## Action

A(컬럼) + B(reaper) 두 파일-배타 그룹으로 병렬 구현, 12 파일 +547/-11.

- `db/models.py` — ActivityLog에 nullable `outcome`/`engine` String(32) + `ix_activity_logs_outcome_ts`(outcome,timestamp), `ix_activity_logs_room_outcome`(room_id,outcome).
- `db/migrations/versions/042_activity_log_outcome_engine.py` — 신규(템플릿 041). revision 042/down 041, forward-only(백필 없음). alembic upgrade 042/downgrade 041 검증.
- `ws/handler.py` `_persist_lifecycle_event` — `outcome=frame.outcome`, `engine=frame.engine` 기록.
- `api/v1/agents.py` `get_agent_activity`+`ActivityLogOut`, `rooms/router.py` `get_room_activity` — optional outcome/engine 쿼리 파라미터 + 필터 + 응답 필드.
- `scheduler/lifecycle.py` `sweep_stale_agents(session_factory, *, threshold_sec=120)` — Agent JOIN Machine, actual_state=running AND last_heartbeat_at IS NOT NULL AND stale AND machine not online(dual-gate) → crashed + state_changed ActivityLog. running-only/IS NOT NULL 가드.
- `observability/metrics.py` — Counter `anygarden_agents_crashed_by_sweep_total`.
- `app.py` `_run_orphan_sweeper` — `_reconcile_agents_by_state` 전에 sweep_stale_agents 호출, `ANYGARDEN_HEARTBEAT_STALE_SEC`(기본 120, 0=skip).
- `tests/test_migrations.py` — head revision 가드 041→042(5곳). 신규/확장 테스트: test_activity_outcome_filter.py, test_ws_handler_lifecycle.py, test_lifecycle.py(TestSweepStaleAgents 4종).

## Decisions

- **outcome/engine 백필 생략**(041과 달리) — 과거 행 details에 일관된 신호가 없고 운영 쿼리는 forward-only. 백필 비용 대비 가치 없음.
- **reaper를 별도 태스크 대신 기존 `_run_orphan_sweeper` 루프에 합류** — lifespan 태스크 추가·종료 와이어링 부담 회피, 최소 변경.
- **dual-gate(stale heartbeat AND machine not online) + running-only/IS NOT NULL** — 단일 조건은 오탐 위험. `starting` 에이전트는 last_heartbeat_at이 NULL(running 전이에만 기록, lifecycle.py ~299-300)이라 반드시 배제.
- **머신 staleness sweep 제외** — `daemon_last_seen_at`이 register 시에만 기록되어 머신 sweep을 켜면 살아있는 전 함대를 첫 tick에 offline 처리. 선행작업 후 별도 PR.
- 가정: threshold 120s ≥ report 주기의 3~4배. 어긋나면 reaper 오탐 — 게이트(=0)로 즉시 비활성 가능, reconnect 시 다음 report가 running 복구.

## Result

- cluster **1091 passed**(직전 5건 실패는 전부 test_migrations head 가드 041 하드코딩 → 042 갱신으로 해소). agent/machine 미변경. ruff 신규 에러 0.
- 효과: 실패/타임아웃 turn이 인덱스 단일 쿼리로 조회 가능; 전원차단 머신의 에이전트 MTTD 20분→~120s, bin-pack이 죽은 에이전트 카운트 중단.
- 후속: Wave 1b(goal CAS+멱등성), 1c(데몬 re-adopt), 1d(비용 원장+invocation-block).

# feat(rooms): autonomous responsibility system MVP — Goal scheduler + executor (#302 Phase 2)

- Commit: HEAD (worklog committed in the same branch)
- Date: 2026-04-28
- PR: TBD (from `feat/302-goals-backend`)
- Stacked on: PR-1 (`feat/302-right-context-rail`, #306)

## Situation

PR-1(#306)이 우측 컨텍스트 사이드바를 ship 했지만 그 안의 "책임/Goal" 자리는 비어 있었다. 에이전트가 매일 호스트 점검·게시글 폴링·표 구매 시도 같은 정해진 책임을 자율적으로 유지하는 메커니즘이 doorae 전체에 부재했다. 기존 #266 의 task auto-execution 은 사용자가 채팅에서 명시적으로 만들어야만 동작하는 1회성 도구였다.

플랜(`.tmp/plan-302-right-context-rail.md`) 의 두 핵심 결정:
1. **GoalRun ↔ Task 통합** (D11) — 별도 `goal_runs` 테이블 도입은 over-engineering. 기존 `tasks` 테이블에 `goal_id` 링크 + 메타데이터 컬럼 추가로 흡수.
2. **`materialize: full | interesting_only` 정책** (D12) — 분 단위 polling Goal 이 Tasks UI 를 운영 로그로 변질시키지 않게, 매 실행을 Task 로 남길지 / 의미 있을 때만 남길지 Goal 등록 시 결정.

## Task

- `agent_goals` 테이블 신규 + `tasks` 테이블 확장(모두 nullable + default) 마이그레이션 작성. 기존 row 가 backfill 없이 살아남게.
- 책임 정의(`Goal`) 와 실행 단위(`Task`) 의 의미 분리를 코드/스키마 레벨에서 명문화.
- 자율 책임 트리거링: cron / interval 트리거를 in-process 폴링 루프로 처리. 각 트리거는 (a) 새 Task 생성 (b) 기존 #266 의 inject_task_assignment_message 로 에이전트 wake.
- 가드레일: cron 1분 미만 거부, 연속 3회 실패 시 자동 pause + 사유 보고, 에이전트 미참여 룸은 등록 시점에 422.
- materialize 정책을 PUT `/tasks/{id}` 핸들러에 훅으로 끼워 넣어 Goal-derived Task 의 완료 시 정책에 따라 보존/삭제.
- Goal CRUD API: POST/GET/PATCH/DELETE + manual run + pause/resume. owner OR admin 권한.
- 기존 Tasks API 에 `?goal_id=` 필터 + TaskOut 에 goal-derived 메타 필드 노출 (PR-3 의 Goal 상세 페이지가 사용).
- FastAPI lifespan 에 scheduler 시작/정지 통합.
- 단위 테스트: 정책 모듈 (cron 검증, next_run 계산, materialize 결정 매트릭스, failure counter).
- 마이그레이션 up/down/up 회귀 검증.

## Action

### Stage 1 — DB 모델 + 마이그레이션 (Phase C)

- `packages/cluster/doorae/db/models.py` (+156 lines):
  - `Goal` 신규 모델 — `assignee_agent_id` (NOT NULL, CASCADE), `owner_id` (NOT NULL, CASCADE), `report_room_id` (nullable, SET NULL — 룸 삭제 시 silent goal 로 다운그레이드), title/spec/status/trigger_type/trigger_config (jsonb)/materialize/consecutive_failures/next_run_at/last_run_at/created_at/updated_at. 인덱스 3개: `(status, next_run_at)` 스케줄러 hot path, `assignee_agent_id`, `report_room_id`.
  - `Task` 확장 — `goal_id`(SET NULL), `triggered_by` (default 'manual'), `spec` (Goal spec snapshot — 변경에 강건), `started_at`/`finished_at`/`agent_session_id`/`tokens_used`/`result_markdown`/`error`/`is_interesting`. 인덱스 `ix_tasks_goal_created` (goal_id, created_at) — 상세 페이지의 "최근 N개 run" 쿼리 backed.
  - 모든 신규 컬럼 nullable + default 값으로 backfill 불필요.
- `packages/cluster/doorae/db/migrations/versions/037_agent_goals_and_task_extension.py` (+220 lines):
  - `op.create_table("agent_goals", ...)` + 3 인덱스
  - `op.batch_alter_table("tasks")` 로 10개 컬럼 추가 + FK + 인덱스
  - downgrade 는 역순 (FK + 컬럼 먼저, 테이블 나중) 으로 안전한 roll-back 보장.
- `tests/test_migrations.py` — head 버전 assertion `036` → `037` (5 spots) 갱신.
- 검증: `uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head` 통과.

### Stage 2 — Policy 모듈 (Phase D 핵심)

- `packages/cluster/doorae/goals/policy.py` (+205 lines):
  - `MIN_INTERVAL_SECONDS = 60` / `GOAL_FAILURE_PAUSE_THRESHOLD = 3` 상수.
  - `validate_trigger_config(trigger_type, config)` — cron 표현식 + 두 시간 샘플로 gap 측정 → 60s 미만이면 거부. interval 정수 + 60s 이상 검증. manual 은 무검증.
  - `compute_next_run_at(trigger_type, config, *, after)` — croniter 또는 timedelta 로 다음 fire 시각 계산. timezone-aware UTC 강제.
  - `MaterializeDecision` enum (KEEP/DELETE) + `materialize_decision()` — 매트릭스: `full` 항상 KEEP / `failed` KEEP / `is_interesting` KEEP / 나머지 (silent success on interesting_only) DELETE.
  - `apply_completion_to_failure_counter()` — 순수 카운터 증감 + 임계 도달 플래그.
- `tests/test_goals_policy.py` (+145 lines, 21 tests): cron 검증 (9개), next_run 계산 (3개), materialize 매트릭스 (5개), failure counter (3개).
- 검증: 21/21 tests green.

### Stage 3 — Executor + Scheduler (Phase D 통합)

- `packages/cluster/doorae/goals/executor.py` (+185 lines):
  - `find_assignee_participant(db, room_id, agent_id)` — 에이전트의 룸 참여 확인 (None 반환).
  - `trigger_goal(db, goal, *, trigger_source)` — Task 생성 → `inject_task_assignment_message` (#266 재사용) → goal `last_run_at`/`next_run_at` 업데이트. participant 없거나 room 사라지면 `GoalExecutionError`.
  - `apply_completion(db, task, *, final_status)` — Task PUT 훅. 실패 카운터 증감 + 임계 도달 시 status='paused'. materialize 정책 적용 → silent success 면 Task 삭제 후 True 반환 (호출자가 fanout 'deleted' 이벤트로 처리).
- `packages/cluster/doorae/goals/scheduler.py` (+135 lines):
  - `GoalScheduler(session_factory, *, poll_interval_seconds=30)` — start/stop 이 idempotent 한 async polling loop.
  - 각 tick 마다 `WHERE status='active' AND next_run_at <= now()` 쿼리, 각 due goal 에 별도 짧은 트랜잭션으로 `trigger_goal` 호출. `GoalExecutionError` 면 자동 pause; 일반 예외는 로그만 + 다음 tick 진행. 단일 bad goal 이 loop 죽이지 않음.
  - 30s 폴링은 정책 floor (60s) 보다 짧아 한 cron 주기 안에 두 번 깨어남이 보장됨 → drift 최소.
- `packages/cluster/doorae/app.py` (+25 lines):
  - lifespan 의 `MachineBus()` mount 다음에 `GoalScheduler()` mount + `start()`. 테스트는 `app.state.goal_scheduler = stub` 으로 우회 가능.
  - shutdown 단계에서 `await scheduler.stop()` (idempotent, 5s timeout, 그래도 안 죽으면 cancel).

### Stage 4 — Goal CRUD API (Phase E)

- `packages/cluster/doorae/api/v1/goals.py` (+325 lines):
  - 엔드포인트 8개: `POST /agents/{id}/goals`, `GET /agents/{id}/goals`, `GET /rooms/{id}/goals`, `GET /goals/{id}`, `PATCH /goals/{id}`, `DELETE /goals/{id}`, `POST /goals/{id}/run`, `POST /goals/{id}/pause`, `POST /goals/{id}/resume`.
  - `_ensure_agent_in_room` — create/update 시점에 에이전트의 룸 참여 검증 → 422 에 actionable 메시지. 스케줄러가 매번 `GoalExecutionError` 잡지 않게.
  - owner OR admin 권한 (`_load_goal_owned`). 다른 사용자는 404 대신 403.
  - PATCH 가 trigger 변경 시 `next_run_at` 즉시 재계산 — 스테일 일정으로 트리거되지 않게.
  - manual run 은 owner/admin 모두 허용; `GoalExecutionError` 면 409 (스케줄러처럼 자동 pause 하지 않음 — 사용자가 직접 fix).
- `packages/cluster/doorae/app.py` — `app.include_router(goals_router)`.

### Stage 5 — Task API 확장 + materialize 훅

- `packages/cluster/doorae/api/v1/tasks.py` (+45 / -10 lines):
  - `TaskOut` 에 `goal_id` / `triggered_by` / `is_interesting` 추가 — PR-3 의 출처 칩(⚙) 렌더에 사용.
  - `list_tasks` 에 `goal_id` 쿼리 파라미터 — Goal 상세의 "최근 N개 run" 쿼리 (인덱스 hit).
  - `update_task` 에 materialize 훅: status 가 done/failed 로 전환되고 `goal_id != NULL` 이면 `apply_completion` 호출. `task_was_deleted=True` 면 fanout 'deleted' 이벤트 + 응답 shape 유지.
- `packages/cluster/pyproject.toml` — `croniter>=2.0` 추가 (~3KB).

### Stage 6 — 검증

- `cd packages/cluster && uv run --with pytest --with pytest-asyncio python -m pytest tests/` → 860 passed, 1 deselected, 1 warning (~4분).
- `uv run ruff check .` 으로 신규/수정 파일 모두 clean. 28개 잔존 경고는 pre-existing scripts/tests, 본 PR 범위 밖.
- 마이그레이션 up/down/up 안전.

## Result

- **자율 책임 시스템 1차 가동** — 사용자가 `POST /api/v1/agents/{id}/goals` 로 책임 등록하면 cluster scheduler 가 자동으로 트리거, 기존 #266 Task auto-execution 흐름으로 에이전트가 실행, `mark_task_status` MCP 로 완료 보고.
- **통합 Task 모델 동작 확인** — Goal-derived Task 와 manual Task 가 같은 `tasks` 테이블, 같은 TaskPanel, 같은 WS 이벤트 스트림 위에서 공존. 출처는 `triggered_by` + `goal_id` 로만 구분.
- **materialize 정책 효과 입증** — `interesting_only` Goal 의 silent success 는 Task 가 자동 삭제 → Tasks UI 가 운영 로그로 변질되지 않음. 실패 / `is_interesting=True` / `materialize=full` 은 모두 KEEP.
- **가드레일 동작** — cron 60s 미만 거부 (HTTP 422 with actionable message), 연속 3회 실패 시 status='paused' + 사유 로그.
- **회귀 zero** — 기존 cluster 860 tests 모두 green, 마이그레이션 round-trip 안전.
- **Phase 3 호환** — TaskOut 의 goal-derived 필드는 PR-1 의 `useRoomTasks.ts` 에서 forward-compat 으로 이미 정의되어 있어 PR-3 프론트엔드 작업에서 추가 schema migration 불필요.
- **신규 코드량** — 약 1,200 라인 추가 (정책 205 + 실행자 185 + 스케줄러 135 + API 325 + 모델 156 + 마이그레이션 220 + 테스트 145 등). materialize 정책의 발견 덕분에 별도 `goal_runs` 테이블·신규 MCP 툴·신규 WS 이벤트 모두 회피 — 추산 -800 라인 절약.

## 후속 (PR-3, Phase H+I+J)

- `lib/goals.ts` API 클라이언트 + `useRoomGoals` / `useAgentGoals` hooks
- 우측 사이드바 `GoalsSection` (룸 책임 카드 list, 다음 실행 카운트다운, run-now/pause)
- `GoalForm` (cron picker / interval / materialize 라디오)
- `AgentSettingsDialog` 에 Goals 섹션 (Tasks 위)
- AgentSettingsDialog 행 클릭 → 룸 점프 + 사이드바 자동 펴짐 + `?goalId=` 강조
- DESIGN.md 시각 점검 + worklog

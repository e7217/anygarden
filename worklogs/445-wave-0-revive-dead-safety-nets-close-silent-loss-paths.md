# fix(reliability): Wave 0 — revive dead safety nets & close silent-loss paths (#445)

- Commit: `aa14524` (aa14524ca718119fda7df48a6b69ffe8530e8e0c)
- Author: Changyong Um
- Date: 2026-06-18T20:35:37+09:00
- PR: #445

## Situation

paperclip(Node.js 에이전트 오케스트레이터)과의 비교 분석 + doorae 8개 신뢰성 영역 감사(적대적 검증)에서 한 가지 진단이 도출됐다: doorae는 turn 결과를 lifecycle 프레임/OTEL로 *관측*하는 능력은 강하나, 그 신호로 *복구*하는 메커니즘이 거의 없다. 특히 "이미 코드에 깔려 있으나 작동하지 않는 안전망"과 "turn이 사용자 통지 없이 사라지는 경로"가 운영 사고의 큰 비중을 차지하는데, 모두 저위험·무마이그레이션으로 고칠 수 있는 것들이었다. 전략은 ADR-006(신뢰성 하드닝 Wave 0~2)에 정리했고 본 커밋은 그 Wave 0이다.

## Task

한 PR로 다음 12개 + ADR을 구현하되, "서버는 스위치보드, not 브레인" / "LLM 판단 최소화" 철학을 깨지 않을 것(새 판단 추가 금지, 결정론적 메커니즘만), 전부 마이그레이션 없이.

- 死안전망 부활: `Task.started_at/finished_at` 상태전이 스탬프 → exec-timeout sweeper 동작
- 무성 유실 차단: gemini 비정상종료 raise, rejected turn 통지, typing-ping await
- 정확성: goal API UTC, anygarden 토큰 커밋 후 캐싱, room_query per-sender
- WS 견고화: replay 페이지네이션, seq dedup, 재접속 jitter+4040, handler 스냅샷
- 운영 가시성: `/healthz` 실제 의존성 체크

## Action

13개 소스 파일 수정 + ADR-006 신규 + 11개 테스트 파일(신규 `test_goals_api_utc.py` 포함). 핵심 변경 지점:

- `packages/cluster/anygarden/mcp/tools.py` `mark_task_status`, `api/v1/tasks.py` `update_task` — is-None 가드로 `started_at`(in_progress)/`finished_at`(done|failed) 스탬프. `goals/sweeper.py`의 exec-timeout 술어(`started_at.is_not(None)`)가 비-goal task에도 발화하게 됨.
- `packages/agent/anygarden_agent/integrations/gemini_cli.py` — `_call_gemini` 비정상 returncode가 `return None` 대신 `raise EngineError`(exit code + stderr snippet). + typing-ping await(codex 패턴).
- `runtime/handler_wrapper.py` — `_REJECTED_NOTICE` 추가, dispatch() rejected 분기에 `client.send`(timeout/failed와 대칭).
- `integrations/{claude_code,openhands_engine}.py` — typing-ping await.
- `api/v1/goals.py` — `datetime.utcnow().astimezone()` → `datetime.now(timezone.utc)` (4곳).
- `app.py` — `/healthz`를 DB `SELECT 1`(wait_for) + gateway supervisor state(FAILED→503, CRASHED→degraded) + 백그라운드 태스크 done() 체크(None=disabled)로 교체.
- `ws/handler.py` — on-connect replay를 커서 페이지네이션(PAGE=200, CEIL=10000, 초과 시 warn)으로.
- `client.py` — per-room `_seen_seqs`(maxlen 256) dedup, `_backoff_with_jitter`/`_close_code` 헬퍼 + 4040 give-up(3회), handler dispatch `list()` 스냅샷 순회.
- `integrations/{delegate,room_query}.py` — `get_event_loop`→`get_running_loop`; room_query 응답을 sender pid dict(last-write-wins)로 키잉, `_deliver_result`도 dict 기반.
- `scheduler/lifecycle.py` — `_acquire_anygarden_token`을 pending(session.info)+after_commit 리스너 2단 캐시로 바꿔 커밋 후에만 durable 캐시; `bump_generation`은 frame을 commit 전에 빌드. #369 within-txn 토큰 재사용 불변 보존.

## Decisions

- **Wave 0 경계 = S·무마이그레이션만.** ActivityLog outcome 컬럼/비용 원장(마이그레이션 동반)은 Wave 1로 분리 — Wave 0의 "무위험 quick win" 성격 유지가 검토·롤백 비용을 최소화하기 때문.
- **rejected 통지는 에이전트측 `client.send`** (클러스터측 inject 기각): `inject_system_message` 헬퍼가 없고 NULL-participant 메시지가 "(left the room)" 고아 버블로 렌더됨 → 프론트 작업 동반 과설계. 에이전트측은 timeout/failed가 쓰는 검증된 경로라 1줄로 대칭 달성.
- **started_at is-None 가드** — goal task는 생성 시(`executor.py`) started_at이 이미 설정되므로 덮어쓰지 않고 첫 전이를 권위로 유지. goal/수동 task 간 exec-timeout 기준점 차이는 둘 다 bounded·안전.
- **토큰 캐시는 2단(pending→after_commit)** — 단순 "커밋 시 캐시"는 #369(한 txn 내 반복 `_build_sync_frame`가 토큰 1개 재사용) 불변을 깨고 row 3개를 만들었음. session.info 스테이징 + `once=True` after_commit 리스너로 해결(초기 `event.remove`는 "deque mutated during iteration"으로 실패).
- **복구(재시도·큐·재adopt)는 범위 밖** — Wave 0는 통지/부활까지만. 자동 복구는 lock/serialization 계약·프로토콜 Literal을 건드려 Wave 1+로.
- 가정: `Task.started_at/finished_at` 컬럼이 nullable로 존재(확인됨), gateway supervisor가 `app.state.llm_gateway_supervisor.state`로 노출(확인됨). 어긋나면 재검토.

## Result

- 전 패키지 회귀 통과: **agent 419 · cluster 1083 · machine 346(+2 skip)**. 신규/수정 테스트 다수(seq dedup, handler 스냅샷, 4040 give-up, rejected 통지, started_at 스탬프, room_query per-sender, healthz 5종, replay >50, 토큰 커밋 게이트, goal UTC seam).
- 효과: exec-timeout 안전망 0→full, gemini/거부/typing 무성경로 제거, WS gap>50 무성손실 제거, 재접속 중복 디스패치 제거, 재시작 401 storm 종료, goal API 비-UTC 드리프트 제거, `/healthz`가 죽은 의존성에 503.
- CI 게이트(machine/agent/cluster `pytest -x`) 모두 green. 프론트엔드/agent-ts는 미변경. ruff는 CI 게이트 아님이며 신규 변경 파일에 새 ruff 에러 0(잔존 F401 `MessageOut`은 HEAD 기존 부채).
- 후속: Wave 1(비용 원장+invocation-block, goal CAS+멱등성, 데몬 re-adopt, agent reaper, ActivityLog outcome 컬럼), Wave 2(active-stop, bounded 룸 큐, task_blockers, CLI telemetry) — 별도 이슈.

# feat(reliability): Wave 1b — goal scheduler exactly-once (CAS + idempotency) + stampede caps (#449)

- Commit: `c4ecdd1` (c4ecdd15f1a4451fdbc2c6e16f4f89ca5e7f2b15)
- Author: Changyong Um
- Date: 2026-06-18T21:33:51+09:00
- PR: #449

## Situation

ADR-006 Wave 1의 스케줄러 정확성 조각. goal 스케줄러는 `_tick`이 due goal을 SELECT해 `trigger_goal`로 발사하고 발사 측이 `next_run_at`을 advance하는 구조였다. CAS도 멱등성도 없어 멀티레플리카/재시작 시 같은 슬롯을 N회 발사할 수 있었고, advance가 `trigger_type != "manual"` 기준(`executor.py:156`)이라 cron/interval goal의 Run-now가 스케줄을 앞당겨 미는 잠복 버그가 있었다.

## Task

한 PR(마이그레이션 043)로, 스위치보드/결정론 철학 유지:
- goal 발사를 원자적 CAS claim으로 → 슬롯당 정확히 1회
- advance를 claim으로 이동(Run-now 스케줄-밀림 버그 동반 수정)
- Task.idempotency_key UNIQUE로 중복 봉쇄
- per-tick cap + in-flight dedup + per-owner cap으로 스탬피드 차단

## Action

5개 파일 수정 + 마이그레이션 043 + 테스트 2개. +260/-34.

- `goals/scheduler.py` — `MAX_GOALS_PER_TICK=25`. `_tick`이 due goal id를 SELECT(asc, limit 25)하고 `_claim_and_fire`로 위임. `_claim_and_fire`: goal 재조회 → in-flight dedup(todo/in_progress Task 있으면 skip, `ix_tasks_goal_created`) → 슬롯 캡처 → 다음 슬롯 계산 → guarded `UPDATE agent_goals SET next_run_at,last_run_at,claimed_at WHERE id AND status='active' AND next_run_at<=now` → `rowcount==1`일 때만 발사 → ORM 객체 동기화(Core UPDATE 후 flush clobber 방지) → `trigger_goal(idempotency_key=...)` → commit.
- `goals/executor.py` — `trigger_goal`에 `idempotency_key` 파라미터(Task에 stamp); `next_run_at` advance 블록 제거(`last_run_at`은 유지); 미사용 `compute_next_run_at` import 제거.
- `db/models.py` — `Task.idempotency_key`(nullable String(128)) + `UniqueConstraint uq_tasks_idempotency_key`; `Goal.claimed_at`(nullable). `ix_tasks_goal_created` 보존.
- `db/migrations/versions/043_goal_claim_and_task_idempotency.py` — revision 043/down 042, batch_alter_table로 컬럼 2개 + unique index, 백필 없음.
- `api/v1/goals.py` — `MAX_ACTIVE_GOALS_PER_OWNER=50`(create 시 초과면 422); `manual_run_goal`은 결정론적 키 계산 후 trigger_goal+commit을 try/except IntegrityError로 감싸 충돌 시 rollback+재조회로 멱등 200(GoalOut).
- `tests/test_migrations.py` head 가드 042→043(5곳). 신규: `test_goals_exactly_once.py`(6), `test_goals_api_idempotency.py`(2).

## Decisions

- **CAS UPDATE + rowcount(RETURNING 비사용)** — RETURNING은 sqlite ≥3.35 필요. guarded single-row UPDATE의 rowcount는 sqlite/PG 공통으로 정확. PG READ COMMITTED에서 패자는 승자 커밋 후 WHERE 재평가로 0행 → exactly-once. (advisory lock 대비 인프라/데드락 표면 없음.)
- **advance를 claim으로 이동(플래그 대신)** — "claim = 슬롯 소비"를 단일 진실로. trigger_goal의 advance를 그대로 두고 CAS만 추가하면 이중 advance로 슬롯 스킵. 이동이 Run-now 스케줄-밀림 버그도 함께 해소.
- **idempotency_key 백필 없음** — nullable+unique는 NULL 다중 허용(sqlite/PG)이라 기존 행 NULL 유지 시 인덱스가 충돌 없이 생성. forward-only로 충분.
- **in-flight dedup = 미완료 Task 검사** — 기존 인덱스로 저렴, 상태가 Task에 있어 별도 동기화 불필요.
- 가정: compute_next_run_at 순수(claim 전 호출 가능, 확인). manual goal next_run_at=None이라 _tick에서 자연 제외(policy.py 확인). 어긋나면 재검토.

## Result

- cluster **1099 passed**(독립 재실행 확인), 1 deselected(기존 libtmux 가드), ruff 신규 에러 0. 마이그레이션 043 upgrade/downgrade 라운드트립 + unique 위반 IntegrityError + ix 보존 검증.
- 효과: goal 정확히-1회(멀티레플리카 correct-by-construction, 분산락 불필요), Run-now 스케줄-밀림 버그 수정, 200-goal 스탬피드 제거.
- 기존 goal 테스트는 advance를 단언하지 않아 head-가드 5곳 외 수정 불필요(계획 추정 ~23보다 적음).
- 후속: Wave 1c(데몬 re-adopt), 1d(비용 원장+invocation-block), Wave 2.

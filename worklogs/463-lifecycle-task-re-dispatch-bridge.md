# feat(reliability): lifecycle→Task re-dispatch bridge (#463)

- Commit: `2b6acef` (2b6acef9f80c370946e1b4f64e29af6502d49238)
- Author: Changyong Um
- Date: 2026-06-19T08:10:27+09:00
- PR: #463

## Situation

ADR-006 Wave 2의 마지막 항목으로 의도적 deferred였다. assignment-originated turn(goal 스케줄러/create_task/auto-route/reassign이 주입한 `[TASK]` 멘션으로 깨어난 turn)이 supervisor에서 rejected/timeout/failed로 끝나면, 해당 Task는 sweeper 타임아웃(실패)이나 사람 개입 전까지 stranding됐다 — 풍부한 lifecycle 관측이 Task 복구로 이어지지 않는 간극. deferred였던 이유: 주입 assignment가 `request_id=None`으로 브로드캐스트돼 에이전트 lifecycle frame도 request_id=None → **request_id↔task 상관관계가 부재**했다.

## Task

cluster측만(에이전트 무변경): (선행) 주입 assignment에 request_id mint + request_id↔task 매핑 영속화, (본체) handler_finished(terminal-non-ok)가 매핑된 turn이면 Task를 todo로 되돌려 1회 자동 재디스패치. 라이브 user-send turn은 무영향, flip-loop 바운드. 마이그레이션 048.

## Action

소스 4 + 마이그레이션 048 + 테스트.

- `db/models.py` — `AgentTurnTask`(agent_turn_tasks): request_id PK, task_id FK(CASCADE), redispatch_count, created_at.
- `db/migrations/versions/048_agent_turn_tasks.py`(down 047) — create/drop. up/down 검증.
- `messages/service.py inject_task_assignment_message` — request_id mint(또는 caller 제공) + `metadata["request_id"]` 스탬프(라이브 경로와 동일 키 → 에이전트 기존 스레딩) + AgentTurnTask insert. 신규 파라미터 `request_id`, `redispatch_count`(carry).
- `ws/handler.py` — `_maybe_redispatch_task`: handler_finished + outcome in {rejected,timeout,failed} + rid 매핑 시 → count<MAX(=1) AND Task 미완료(todo/in_progress)면 todo 리셋(assigned_at 갱신/started_at None/error=redispatch:<outcome>) + 재inject(count+1). `_persist_lifecycle_event` 직후 호출. own session + 전면 try/except.
- `observability/metrics.py` — task_redispatched_total{outcome}.

## Decisions

- **전용 agent_turn_tasks 매핑 테이블** — redispatch_count 가변 상태 + request_id PK 조회. ActivityLog.details JSON 결합은 카운트 갱신이 지저분.
- **inject에서 request_id mint(선행)** — assignment 주입은 라이브 fan-out 미경유라 request_id=None이었음. mint가 상관관계의 선행 조건. inject가 단일 관문(executor/create_task/auto-route/reassign 4 callers 확인)이라 한 곳 수정으로 전 경로 커버.
- **스코프 = 매핑 있는 turn만** — 라이브 user-send/peer-handoff turn은 AgentTurnTask 행이 없어 lookup None → 무영향. 라이브 turn 재디스패치는 무의미·위험.
- **flip-loop 바운드 = carry count, MAX=1** — 매 재wake가 새 request_id→새 행이라 count를 체인으로 carry해야 차단. 초과 시 todo로 두고 sweeper/사람.
- **cancelled 제외** — 사용자/budget active-stop의 의도적 중단이라 재디스패치 부적절(canceller와 싸우지 않음). queued/retrying/retry_exhausted도 Wave 2b 소관.
- **own session + 방어 래퍼** — 재디스패치 실패가 lifecycle ActivityLog 커밋을 롤백하지 않게. best-effort 복구.

## Result

- cluster **1175 passed**(+11, 독립 재실행), ruff clean. 마이그레이션 048 up/down + head 검증.
- 검증: inject가 request_id+매핑 생성; assignment turn rejected/timeout/failed→Task todo+재inject 1회(count 1); 2회째→재디스패치 안 함(바운드); 라이브 turn(매핑 없음)→무영향; ok/cancelled/완료 Task→무영향.
- 효과: 실패한 assignment turn이 영구 stranding 대신 1회 자동 복구 — 관측↔복구 간극 해소. **ADR-006 Wave 0~2 전 항목 완료.**

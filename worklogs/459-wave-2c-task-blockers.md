# feat(reliability): Wave 2c — task_blockers dependency relation (wake on blocker completion) (#459)

- Commit: `e922da9` (e922da94b9138720d2896850664780dddd5da580)
- Author: Changyong Um
- Date: 2026-06-18T23:43:18+09:00
- PR: #459

## Situation

차단된 Task가 *무엇을* 기다리는지 기록할 1급 관계가 doorae엔 없었다. 그래서 블로커가 끝나도 차단 Task는 sweeper 타임아웃(실패)이나 사람의 수동 재라우팅 전까지 inert했다. paperclip의 blocks 관계 + blockers-resolved wake에 대응하는 누락.

## Task

cluster에, 마이그레이션 동반: task_blockers 다대다 관계 + assignee-only MCP 툴(add/clear, 사이클 가드) + 블로커 terminal 시 dependent 자동 todo 복귀+재wake. mention-wake 재사용.

## Action

소스 4 + 마이그레이션 046 + 테스트.

- `db/models.py` — `TaskBlocker`(task_blockers): task_id/blocked_by_task_id FK(ON DELETE CASCADE), 복합 PK + ix(blocked_by_task_id) 역조회용.
- `db/migrations/versions/046_task_blockers.py`(down 045) — create_table + cascade FK + 인덱스. up/down 라운드트립 검증.
- `mcp/tools.py` — `add_task_blocker`/`clear_task_blocker`(assignee-only, mark_task_status 인증 패턴) + `resolve_task_blockers` + `_is_transitively_blocked_by`(BFS+visited 사이클 가드). 자기참조/사이클 거부, idempotent. TOOL_SCHEMAS 등록.
- `mcp/router.py` — 두 툴 디스패치 + mark_task_status terminal 후 woken dependent fanout(mention + task.updated).
- `api/v1/tasks.py` — REST update_task terminal 전이에도 resolve_task_blockers + fanout.
- `messages/service.py inject_task_assignment_message` 재사용.

## Decisions

- **별도 관계 테이블(다대다)** — 한 Task가 N개에 막힐 수 있어 단일 컬럼 불가. 역조회 인덱스로 resolve 저렴.
- **재wake = inject_task_assignment_message 재사용** — 검증된 mention-wake 경로. 새 경로 불필요.
- **add 시점 BFS 사이클 가드(+visited)** — A blocks B blocks A면 영원히 안 풀림. 이행 폐포로 거부, 기존 데이터 사이클도 visited로 무한루프 방지.
- **모든 블로커 terminal일 때만 wake** — 부분 해제로 wake하면 아직 다른 블로커에 막힌 Task가 헛돎. 마지막 해제 시에만 todo 복귀.
- **MCP + REST 양 terminal 경로 훅** — 완료가 두 경로 모두에서 가능하므로 누락 없이.
- **WS fanout 추가**(계획 보강) — 재wake가 always-on assignee에 실제 도달하도록 create_task fanout 패턴 차용.

## Result

- cluster **1155 passed**(+15, 독립 재실행), ruff clean. 마이그레이션 046 up/down + cascade/index 검증.
- 사이클 거부·비assignee 거부·자기참조 거부; 단일 블로커 해제→wake, 다중 블로커 부분 해제→미wake/전체 해제→wake; MCP·REST 양 경로 검증.
- 효과: 차단 Task가 의존성 완료 시 자동 todo 복귀+재wake(현재는 sweeper/수동까지 inert).
- 후속: CLI telemetry(2d), lifecycle→Task 재디스패치(별도, request_id↔task 상관 선행 필요).

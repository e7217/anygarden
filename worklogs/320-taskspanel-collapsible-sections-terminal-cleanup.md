# feat(agents): TasksPanel collapsible sections + terminal cleanup (#320)

- Commit: `bbaf278` (bbaf278973d57060d18a3bcadef7f0ec4741d12e)
- Author: Changyong Um
- Date: 2026-04-29T00:54:04+09:00
- PR: #320 (issue)

## Situation

Agent settings dialog의 Tasks 패널은 agent에 할당된 모든 task를 status별 섹션으로 보여주는데, 누적될수록 dialog 전체 길이가 길어져 작업성이 떨어지고 있었다. 더 큰 문제는 표시 버그였다 — `STATUS_ORDER`에 `failed`가 빠져 있고 `m.get(t.status) ?? m.get('todo')!` 폴백이 있어서, goals sweeper가 `failed`로 마킹한 stuck task가 `Todo` 섹션에 잘못 표시되고 있었다 (`goals/sweeper.py:94`, `goals/policy.py:157` 참조). 종료된 task를 정리할 수단도 없어 reference 가치가 작은 row가 영구적으로 자리를 차지했다.

## Task

- TasksPanel을 status 5종(`todo`/`in_progress`/`blocked`/`done`/`failed`) 모두 표시하도록 정상화.
- 길이 부풀림을 막을 접기/슬라이스 UX 추가.
- terminal task를 정리할 수 있는 단일 + bulk delete 경로.
- 기존 `useRoomTasks`가 사용 중인 단일 `DELETE /api/v1/tasks/{task_id}` 권한 모델은 회귀 위험 때문에 손대지 않기.
- DESIGN.md 적합성 유지 (warm neutral, whisper border, single accent).
- 표시 버그를 단위 테스트로 봉쇄.

## Action

**Backend** (`packages/cluster/doorae/api/v1/agents.py`):
- 신규 `bulk_delete_agent_tasks` 핸들러를 `list_agent_tasks` 바로 위에 추가. `DELETE /{agent_id}/tasks?status=<done|failed>` 라우트, `Depends(get_admin_identity)` 게이트.
- `_TERMINAL_TASK_STATUSES = frozenset({"done", "failed"})` 모듈 상수로 active vs terminal 경계 명문화.
- 두 단계 delete: `SELECT Task.id … JOIN Participant` → `DELETE FROM tasks WHERE id IN (…)`. SQLAlchemy/SQLite의 "no synchronize-able delete with a join" 제약 회피.

**Backend tests** (`packages/cluster/tests/test_agent_tasks_aggregation.py`):
- 새 `bulk_env` 픽스처 — `bot`(todo+done×2+failed) + `other`(done×1, 다른 room)로 cross-agent isolation을 직접 검증.
- 5개 케이스: `done` clear, `failed` clear, non-admin 403, non-terminal status 400, missing status 400/422.

**Frontend** (`packages/cluster/frontend/src/components/agent-settings/TasksPanel.tsx`):
- 전면 리팩터. `STATUS_ORDER`를 5종으로 확장, 의미 기반 `ACTIVE_STATUSES` / `TERMINAL_STATUSES` 분리.
- `groupTasksByStatus`를 export된 순수 함수로 추출. unknown status는 silent todo 흡수에서 `console.warn` + drop으로 전환.
- 각 status 섹션을 inline `<details>/<summary>`로 감싼다 (dialog-level `CollapsibleSection`은 카드 chrome이라 nested card-in-card가 되어 재사용하지 않음). Active 3개 `open`, Terminal 2개 default closed.
- "Show all (n)" — 21개 이상이면 ASC `created_at` 배열의 tail 20개만 렌더, 상태 보존을 위한 `Partial<Record<Status, boolean>>` showAll state.
- 섹션 본문 `max-h-80 overflow-y-auto`로 nested scroll(외부는 dialog body가 이미 잡고 있음).
- terminal 섹션 항목에 hover-only `Trash2` (`group/row` + `opacity-0 group-hover/row:opacity-100`). 헤더에 "Clear all" 버튼 — `e.stopPropagation()`으로 `<details>` toggle과 격리, shadcn/ui `Dialog`로 confirm.
- 단일 delete는 기존 `DELETE /api/v1/tasks/{task_id}` 재사용, bulk는 새 admin-only 엔드포인트 호출. 둘 다 끝나면 `fetchTasks()`로 invalidate.

**Frontend tests** (`packages/cluster/frontend/src/components/agent-settings/TasksPanel.test.ts`):
- 5개 케이스로 grouping 봉쇄: STATUS_ORDER 형태, `failed` 격리(폴백 회귀 가드), 5종 모두 분리, unknown drop + warn, ASC 순서 보존.

## Decisions

`.tmp/plan-320-tasks-card-collapsible-cleanup.md`의 §3.2 "의사 결정 과정"이 1차 자료. 구현 중 두 곳에서 plan에 적힌 안에서 이탈했다.

**섹션 접기 컴포넌트 — plan은 "기존 `CollapsibleSection`에 `headerExtra` prop 추가" 였으나, 코드 확인 후 inline `<details>`로 변경.**
- 기각: dialog-level `CollapsibleSection`은 `SECTION_CARD_CLASS`로 흰 카드 chrome을 가지고 있어, TasksPanel(이미 외부 `<Section>` 카드 안에 들어감) 안에서 재사용하면 카드-안-카드가 됨. DESIGN.md §4의 단일 카드 원칙과 시각적으로 충돌.
- 채택: TasksPanel 안에 inline `<details>/<summary>`. 기존 h4 chrome(11px uppercase muted)를 그대로 summary에 옮겨, 시각적 변화는 chevron 추가뿐. native `<details>`의 키보드/스크린리더 동작은 그대로 보존.
- 결정적 근거: 시각 단순성. dialog-level helper를 건드리지 않으니 blast radius도 작음.

**단일 `DELETE /api/v1/tasks/{task_id}` 권한 점검 — plan은 "현재 무방비라면 admin/agent owner 게이트 추가" 였으나, deferred.**
- 발견: `useRoomTasks.ts:161`이 동일 엔드포인트를 room-view에서 사용 중. 권한을 admin/agent owner로 좁히면 일반 user의 room 내 task 삭제가 깨진다.
- 기각: admin 게이트 추가 — room-view 회귀 즉시 발생.
- 기각: agent owner 게이트 — task는 room-scoped이지 agent-owned이 아니라 매핑이 부자연스러움.
- 채택: 기존 `get_current_identity`(인증만) 유지. PR/commit body에 deferred 사유 명시. 새 bulk endpoint만 admin-only로 일관성 확보.
- 가정: room-scoped permission은 별도 트랙(예: room membership 미들웨어)으로 다뤄야 함. 본 PR은 #320 스코프 안에서 끝낸다.

**`blocked`의 분류 — Active로 결정.**
- `routing/router.py:165`가 `["todo", "in_progress", "blocked"]`을 active set으로 처리하고 있어 시스템 의미와 일치. 사용자가 안 보면 stuck task가 invisible해지는 위험을 회피.
- 기각된 v1 안: "done과 함께 terminal로 묶어 기본 접힘" — semantic 오류였다.

**슬라이스 — 클라이언트 vs 서버 페이지네이션.**
- 채택: 클라이언트, ASC 배열의 tail 20개. 백엔드 contract 변경 0.
- 미해결: agent당 task가 1k+ 누적되어 fetch latency가 문제되면 서버 페이지네이션 후속 PR.

**Cascade 우려 — task에 FK 자식 테이블이 없어 (`grep ForeignKey.*tasks` 0건) hard delete 안전.**

## Result

- 백엔드 913 + 신규 5건, 프론트엔드 380 + 신규 5건 모두 그린.
- `npm run build` (tsc + vite) 통과. ruff (수정 Python 파일) clean.
- `failed` task가 더 이상 Todo 섹션에 새는 일 없음 — TasksPanel.test.ts:36-46이 회귀 봉쇄.
- terminal 섹션 hover에 trash 노출, "Clear all"이 confirm dialog → admin-only bulk endpoint를 한 번 호출.
- DESIGN.md 적합성: warm neutral 팔레트 유지, destructive 강조(빨강) 없음 — `<button>` hover state로 충분.
- 수동 smoke test(dev 서버 / admin 로그인)는 환경상 사용자 검증으로 남김. plan §4.5.4의 시나리오를 그대로 재사용 가능.
- 관련: #319(backend `TASK_STATUS_VALUES`에 `failed` 추가)가 머지되면 enum 단일 소스화로 더 깔끔해짐. 본 PR은 독립 진행 가능.

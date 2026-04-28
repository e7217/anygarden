# feat(rooms): auto-rep invariant + assignee picker in right rail (#312)

- Date: 2026-04-28
- PR: TBD (from `feat/312-auto-rep-and-assignee-picker`)

## Situation

우측 사이드바의 두 추가 버튼이 UX 결손을 가지고 있었다.
1. **Tasks**: `right-rail/TasksSection.tsx` 의 inline 폼에 assignee picker 가 없어 `assignee=null` task 가 생성됨. `inject_task_assignment_message` 가 NULL assignee 에 대해 None 을 반환하므로 합성 멘션이 룸에 안 떨어지고 어떤 에이전트도 깨어나지 않음 → task 가 todo 상태로 영원히 남음. PR-1 (#306) 머지 시점에 폭 가독성을 위해 의도적으로 dropped 한 결정의 부산물.
2. **Goals**: `GoalForm` 이 implicit `defaultAgent = candidateAgentIds[0]` 으로 prefill — 멀티-에이전트 룸에서 누가 받는지 사용자에게 가시화 안 됨.

또 후속 #313 (🪄 batch auto-route) 이 rep 에이전트를 라우팅 권한 주체로 사용 예정인데, 현재 `Room.representative_agent_id` 가 NULL 가능 — admin 이 명시 set 해야만 채워졌다. 빈 NULL 상태가 정상 케이스로 존재하면 라우팅 fallback 정책이 복잡해진다.

## Task

- 서버 invariant: 빈 룸에 첫 에이전트 추가 시 rep 자동 set, rep 제거 시 다음 에이전트로 승계.
- TasksSection 의 추가 폼에 assignee chip dropdown + 행 hover 시 reassign dropdown.
- GoalForm 의 implicit `defaultAgent` 제거 → explicit `<select>` (Goal 은 NOT NULL 이라 `Unassigned` 옵션 없음).
- GoalsSection row 에 assignee 이름 inline 표시 (한 번에 누가 책임지나 가시화).
- 마이그레이션 0건, 회귀 zero, ~150~200 LOC 목표.

## Action

### Stage 1 — 백엔드 invariant (TDD, Phase A)

- `packages/cluster/tests/test_room_rep_invariant.py` (신규, +320 lines, 8 tests):
  - **TestAutoSetRepOnFirstJoin** (4 cases): 첫 add 시 rep set / 두 번째는 변경 없음 / admin override 보존 / user 추가는 rep 영향 없음.
  - **TestRepSuccession** (4 cases): 다음 에이전트로 승계 (joined_at) / id tie-breaker 결정성 / 마지막 에이전트 제거 시 NULL / 비-rep 제거 no-op.
  - 모든 테스트 Red 상태로 시작 → 단계적 Green.

- `packages/cluster/doorae/rooms/membership.py` (+50 lines):
  - `ensure_agent_in_room` 에 invariant 분기 추가 — `created=True` 일 때 `Room.representative_agent_id IS NULL` 면 그 agent 로 set. 같은 트랜잭션에 commit (참여자 insert 와 rep set 이 atomically).
  - 신규 helper `_set_next_rep_after_removal` — rep 제거 시 다음 에이전트 (joined_at ASC, Participant.id ASC) 로 승계. 비-rep 제거에는 no-op. 마지막 에이전트면 NULL.

- `packages/cluster/doorae/rooms/router.py` (-7 / +13 lines):
  - line 558-565 의 "rep 면 NULL 로 set" 로직을 `_set_next_rep_after_removal` 호출로 교체. 같은 트랜잭션에서 처리.

- 검증: `uv run pytest tests/test_room_rep_invariant.py tests/test_membership.py tests/test_rooms.py tests/test_agents_api.py` → 113 passed (8 new + 105 existing). 풀 cluster 875 passed.

### Stage 2 — Tasks 직접 배정 picker (Phase B)

- `packages/cluster/frontend/src/components/right-rail/TasksSection.tsx` (+90 / -10 lines):
  - 추가 폼에 새 row: title input → 다음 row 에 assignee `<select>` + `+` 버튼.
  - `agentParticipants` useMemo — `kind === 'agent'` 만 필터, alphabetical sort.
  - `singleAgentRoom` 자동 선택 + select disabled (`opacity-70`) 으로 read-only 보임.
  - useEffect 로 candidate 변동 시 선택 자동 보정 (room 떠난 에이전트 선택 상태 → empty).
  - 행 hover 시 reassign `<select>` — `opacity-0 group-hover:opacity-100`. legacy `TaskPanel.tsx:191-212` 패턴 미러.
  - submit 후 single-agent 룸은 selection 보존, 멀티는 reset.

### Stage 3 — Goals explicit picker + row assignee (Phase C)

- `packages/cluster/frontend/src/components/goal-form/GoalForm.tsx` (+50 / -10 lines):
  - 시그니처 변경: `agentId: string` (hidden prop) → `roomAgents: GoalFormAgentOption[]` + 선택적 `defaultAgentId?: string | null`.
  - 새 export `GoalFormAgentOption` (id + name) — caller 가 derived list 를 가볍게 전달 가능.
  - 폼 내부 새 state `assigneeAgentId` — default = `defaultAgentId ?? roomAgents[0]?.id`.
  - 새 `<select>` 행 (제목 다음, Spec 앞) + `aria-required=true` + 1-candidate 시 disabled + caption.
  - `submit()` 가 `createGoal(assigneeAgentId, ...)` 로 호출. 빈 assignee 시 inline error ("Agent 를 선택해 주세요.") — server 422 가기 전 catch.

- `packages/cluster/frontend/src/components/right-rail/GoalsSection.tsx` (+30 / -5 lines):
  - prop 변경: `candidateAgentIds: string[]` → `agentParticipants: Participant[]`.
  - useMemo 로 `formAgents` (id+name 옵션) 와 `agentNameById` 맵 도출.
  - `+` 버튼 게이트 `defaultAgent` → `hasCandidates` (formAgents.length > 0).
  - GoalForm 호출에 `roomAgents={formAgents}` 전달.
  - row 의 메타 라인 갱신: `<assignee> · <trigger> · next ...` 형태. 스테일 assignee 는 id 짧게 fallback.

- `packages/cluster/frontend/src/components/RightContextRail.tsx` (+8 / -7 lines):
  - `candidateAgentIds` useMemo 제거, `agentParticipants` 로 교체.
  - GoalsSection 호출에 새 prop 전달.

- `packages/cluster/frontend/src/components/agent-settings/GoalsPanel.tsx` (+10 / -3 lines):
  - prop `agentName?: string` 추가. `[{id: agentId, name: agentName || ...}]` 단일 element 배열로 GoalForm 호출.

- `packages/cluster/frontend/src/components/AgentSettingsDialog.tsx` (+3 / -1 lines):
  - GoalsPanel 호출에 `agentName={agent?.name ?? ''}` 추가.

### Stage 4 — 검증

- `cd packages/cluster/frontend && npm run build` → 9.65s clean (tsc 포함, 회귀 zero).
- `npx vitest run` → 37 files, 375 tests (변화 없음 — 본 PR 은 component 시그니처 변경이지만 기존 테스트가 바뀐 prop 을 안 건드림).
- `cd packages/cluster && uv run pytest tests/` → 875 passed (867 pre-#312 + 8 new).
- 마이그레이션 변경 0 (model 컬럼 변경 없음).

## Result

- **rep invariant 도입** — 모든 비어있지 않은 (에이전트 1명 이상) 룸은 항상 rep 가 있음. #313 의 batch auto-route 가 fallback 정책 없이 rep 를 의지할 수 있는 토대 확보.
- **rep 승계 자동화** — 사용자가 rep 를 RoomHeader 에서 manual 변경한 결과는 보존되되, 그 rep 가 룸에서 제거되면 다음 에이전트로 자동 승계. 빈 룸만 NULL.
- **Tasks `+` 가 의도대로 동작** — 사용자가 picker 명시 선택 시 즉시 agent 가 깨어나 (inject_task_assignment_message → decide_policy mention path) task 처리. Unassigned default 는 "메모만" 의도 보존.
- **Goals 의 누가 가시화** — 폼에 explicit Agent select + 카드 row 에 assignee 이름. 멀티-에이전트 룸에서 사용자가 매번 명시 결정 가능.
- **회귀 zero** — 기존 875 cluster + 375 frontend 테스트 모두 그대로 green. legacy `TaskPanel` 의 동작 변경 없음 (rail-specific 컴포넌트만 picker 추가).
- **신규 코드량** — 약 684 라인 추가 / 46 라인 제거. 그 중 단위 테스트 320 라인 (47%). 마이그레이션 0건, 신규 dep 0건.

## 후속 (#313 PR)

- 🪄 "Auto-route unassigned" 버튼 — `POST /api/v1/rooms/{id}/auto-route-unassigned`. rep 에이전트에 WS 메시지로 라우팅 요청, JSON 응답 파싱, 일괄 배정.
- TasksSection 헤더에 ⚡ 아이콘 버튼 + spinner UI.
- 본 PR (#312) 의 invariant 가 보장되어 fallback 정책 단순.

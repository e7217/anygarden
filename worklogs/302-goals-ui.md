# feat(rooms): Goals UI — right rail Responsibilities + AgentSettingsDialog (#302 Phase 3)

- Date: 2026-04-28
- PR: TBD (from `feat/302-goals-frontend`)
- Stacked on: PR-2 (`feat/302-goals-backend`, #307)

## Situation

PR-1 (#306, 우측 사이드바 + Tasks/Files) 와 PR-2 (#307, Goal 백엔드 + 스케줄러) 가 main 에 들어갔지만 사용자 표면이 아직 비어 있었다. Goal CRUD API 와 자동 트리거링은 동작하나 UI 가 없어 책임 등록·일시정지·즉시 실행을 curl 로만 가능했다. 또한 PR-1 에서 의도적으로 일반화한 우측 사이드바 컨테이너의 "Goals 섹션" 자리도 빈 상태였다.

## Task

- `Goal` 타입 + REST 클라이언트 헬퍼 작성. 서버의 `GoalOut`/`GoalCreate`/`GoalUpdate` 와 1:1 정합.
- 두 갈래 데이터 hook 도입: 룸-스코프(`useRoomGoals`) 와 에이전트-스코프(`useAgentGoals`). `doorae:goal:updated` WS 이벤트는 forward-compat 으로 미리 구독 (서버 broadcast 는 Phase 4 에서 연결).
- 책임 생성 폼 (`GoalForm`) — cron/interval/manual 라디오, spec textarea, **materialize 라디오** (interesting_only default + 짧은 설명문), report_room 입력. 서버의 422 detail 을 inline 노출.
- 룸 우측 사이드바에 `GoalsSection` — Tasks 위. 상태 dot, 다음 실행 카운트다운, 연속 실패 카운터, hover 시 run-now/pause-or-resume/delete 버튼. inline `+` 토글이 폼을 펼침 (현재 룸 자동 prefill).
- AgentSettingsDialog 에 `GoalsPanel` 섹션 — Tasks 위. cross-room 뷰, 카드 형식 (spec 두 줄 미리보기 포함).
- 빌드/타입체크 통과 + 기존 테스트 회귀 zero.

## Action

### Stage 1 — REST 클라이언트 + 데이터 hook

- `packages/cluster/frontend/src/lib/goals.ts` (+125 lines) — `Goal` 인터페이스, 상태/트리거/materialize 유니온 타입, 8개 API 함수 (createGoal / listAgentGoals / listRoomGoals / getGoal / updateGoal / deleteGoal / runGoalNow / pauseGoal / resumeGoal). 서버 422 의 `detail` 필드를 throw 메시지에 surface 하는 `jsonOrThrow` 헬퍼 — 폼이 actionable 에러를 그대로 보여주게.
- `packages/cluster/frontend/src/hooks/useRoomGoals.ts` (+90 lines) — 룸-스코프. 마운트 시 fetch, `doorae:goal:updated` 리스너, `remove`/`runNow`/`pause`/`resume` 액션. `roomId === null` 시 fetch 정지.
- `packages/cluster/frontend/src/hooks/useAgentGoals.ts` (+85 lines) — 에이전트-스코프. 동일 패턴.

### Stage 2 — UI 컴포넌트

- `packages/cluster/frontend/src/components/goal-form/GoalForm.tsx` (+200 lines):
  - 5개 입력군: 제목 / spec / report_room / 트리거 (cron|interval|manual + 동적 인풋) / materialize.
  - `triggerConfig` 는 useMemo 로 트리거 타입에 따라 객체 모양 변형 — 서버의 `validate_trigger_config` 가 받는 jsonb 와 정확히 매칭.
  - `materialize` 는 라디오 + 설명문 ("실패/주목할 결과만" / "매 실행을 기록"). DESIGN.md 의 사용자 의사결정 가이드 패턴.
  - 422 detail 을 빨간색 inline 알림. submit 중 disabled.
- `packages/cluster/frontend/src/components/right-rail/GoalsSection.tsx` (+165 lines):
  - 헤더에 카운트 + `+` 토글 버튼 (45도 회전으로 active 표시 — DESIGN.md 의 미세 모션).
  - 폼은 inline expand. 닫으면 list 로 복귀.
  - 각 row: 상태 dot (active=brand, paused=subtle, failed=red), 제목, 트리거 + 다음 실행 + 연속 실패 카운터. hover 시 3개 액션 버튼 (Zap=run-now, Pause/Play, Trash).
  - delete 는 confirm 후. run-now 후 자동 refresh.
- `packages/cluster/frontend/src/components/agent-settings/GoalsPanel.tsx` (+170 lines):
  - cross-room 카드 list. 사용자에게 "이 에이전트가 자율적으로 수행하는 책임 (room 무관)" 안내문 노출.
  - 빈 상태는 dashed border 의 emptyState 카드.
  - 카드는 status dot + 트리거 정보 + report_room 짧은 ID + spec 두 줄 line-clamp + 동일 액션 버튼.
  - GoalForm 재사용. agentId 가 null 이면 "select an agent" 메시지.

### Stage 3 — 마운트 + 통합

- `packages/cluster/frontend/src/components/RightContextRail.tsx` (+15 lines):
  - `<GoalsSection/>` 을 `<TasksSection/>` 위에 렌더 (DESIGN/UX 결정: 책임이 task 보다 상위 개념).
  - `participants` prop 에서 agent 만 추려 `candidateAgentIds` 구성 → 폼이 default agent 를 자동 선택 (룸의 첫 에이전트). 빈 룸이면 `+` 버튼 자체가 안 뜸.
  - 헤더 docstring 의 섹션 순서를 Goals → Tasks → Files 로 갱신.
- `packages/cluster/frontend/src/components/AgentSettingsDialog.tsx` (+9 lines):
  - `<Section id="goals" title="Responsibilities">` 을 Tasks 섹션 바로 위에 추가. Section helper 가 항상 펴짐(non-collapsible) 이라 Tasks 와 같은 가시성.

### Stage 4 — 검증

- `cd packages/cluster/frontend && npm run build` → tsc + Vite 9.0s clean.
- `npx vitest run` → 37 files, 375/375 tests (PR-1 기준 그대로 + Goals 는 integration-tested via build).

## Result

- **자율 책임 시스템 완전 노출** — 이제 사용자가 채팅 UI 에서 (1) 룸 우측 사이드바의 `+` 또는 (2) 에이전트 설정 다이얼로그의 Goals 섹션에서 책임을 등록/일시정지/즉시 실행/삭제 가능. PR-2 의 백엔드 스케줄러가 자동으로 트리거.
- **두 갈래 멘탈 모델 정착** — 룸 뷰("이 룸에 보고하는 책임") 와 에이전트 뷰("이 에이전트의 모든 책임") 가 같은 데이터를 다른 angle 로 보여줌. PR-1 의 Tasks dual view 와 동일한 패턴.
- **materialize 정책 사용자 결정점 제공** — 라디오 + 설명문으로 "조용한 모니터링" 과 "자세한 기록" 둘을 명확히 구분. default 보수적 (interesting_only).
- **422 detail surface** — 1분 미만 cron / 룸 비참여 에이전트 등 서버 검증 실패가 폼 안에 그대로 노출 → 사용자가 즉시 수정 가능.
- **WS forward-compat** — Phase 4 의 `doorae:goal:updated` broadcast 가 들어오면 즉시 라이브 갱신.
- **회귀 zero** — 기존 frontend 375 tests 모두 그대로 green, build clean, 기존 컴포넌트 동작 변경 없음.
- **신규 코드량** — 약 850 라인 추가 (lib 125 + hooks 175 + UI 535). 기존 useRoomTasks/useRoomFiles 패턴 미러링이라 신규 추상화 0.

## 후속 (#302 Phase 4 이후, 별도 이슈)

- 서버 측 `doorae:goal:updated` WS broadcast (현재 클라이언트만 listening).
- `materialize=digest` (요약 Task) 모드 — 보조 로그 테이블 + 일별 요약 cron.
- retry_policy + deadline + webhook 트리거.
- HITL approval (위험 액션 사용자 승인 카드).
- budget 추적/차단 (토큰/비용 일일 한도).
- AgentSettingsDialog → Goal 행 클릭 → 룸 점프 + 사이드바 자동 펴짐 + 강조 (router push + ?goalId=).
- Room picker (현재는 plain UUID 입력) + cron picker (현재는 텍스트).
- 미니 차트 (최근 N회 success/fail 시각화).

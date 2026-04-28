# feat(rooms): right context rail — Tasks/Files 사이드바 통합 (#302 Phase 1)

- Commit: `244095a` (244095a539e7dea1815d370316824f748fd1aae4)
- Author: Changyong Um
- Date: 2026-04-28T16:41:01+09:00
- PR: TBD (from `feat/302-right-context-rail`)

## Situation

채팅 페이지에서 Tasks 와 Chat 이 같은 메인 영역의 **탭 토글**(`ChatPage.tsx:533-564`)로 분리되어 있었다. 사용자는 task 상태나 공유 파일을 확인하려면 채팅 흐름을 끊고 탭을 갈아탄 뒤 다시 돌아와야 했다. 룸 컨텍스트(Tasks, Shared Files)가 흩어져 있고, 향후 자율 책임/Goal 시스템(#302 Phase 2)을 통합할 자리도 없었다. 게다가 #266 에서 도입된 dual room/agent task views 모델은 이미 "에이전트가 1차 소유, 룸은 협업/보고 채널" 이라는 추상을 만들어 두었지만, UI 가 그 추상을 반영하지 못한 채 룸-탭에 가둬져 있었다.

## Task

- 채팅 메인 영역을 잠식하는 탭 모드를 제거하고, 룸 컨텍스트(Tasks + Shared Files)를 우측 컨텍스트 사이드바(Right Context Rail)로 이전한다.
- 좌측 네비게이션 사이드바(#106/#115/#117) 의 collapse 패턴을 1:1 미러링하되 default polarity 만 반전(우측은 기본 접힘)한다 — 채팅 캔버스가 폭 우선, 사용자가 의식적으로 컨텍스트를 펼쳐 본다.
- TaskPanel(#266) / RoomSharedFilesDialog(#246) 의 데이터 평면을 hook으로 추출해 레거시 컴포넌트와 새 우측 사이드바 섹션이 같은 캐시 + 같은 WS 이벤트 스트림을 공유하도록 한다.
- 새 항목(task) 도착 알림 표식을 사이드바 토글 버튼에 다는데, 카운터가 아닌 단순 dot — DESIGN.md 의 "느껴질 정도로만" 미감 따라.
- 모바일(<md) 은 좌측 사이드바와 동일한 backdrop + slide-in drawer 패턴.
- 기존 chat surface 의 "공유 파일" Paperclip 진입점은 사이드바 FilesSection으로 단일화하여 제거. 다이얼로그 자체는 deep-link 호환을 위해 살림.
- Phase 2(자율 책임 시스템 — Goal scheduler/executor + DB 확장)가 들어올 때 `<GoalsSection/>` 을 `<TasksSection/>` 위에 추가하는 일이 구조 변경 없이 끝나도록 컨테이너를 일반화한다.

## Action

### Stage 1 — 우측 사이드바 인프라

- `packages/cluster/frontend/src/hooks/useRightSidebarLayout.ts` (75 lines, new): `useSidebarLayout.ts` 의 byte-for-byte 미러. 차이는 두 곳:
  - 스토리지 키 `doorae_right_sidebar_collapsed`
  - `readInitial()` 의 default polarity 반전 — `localStorage` 값이 없으면 `true` (collapsed) 반환
- `packages/cluster/frontend/src/hooks/useRightSidebarLayout.test.ts` (78 lines, new): 6개 contract 테스트 — throw, default 접힘, hydrate true, hydrate false, toggle, setCollapsed
- `packages/cluster/frontend/src/App.tsx` (10 lines edited): `<RightSidebarLayoutProvider>` 를 `<SidebarLayoutProvider>` 안쪽에 nest. 라우팅 로직 영향 없음, 좌측과 같은 "data → layout" 순서 유지.

### Stage 2 — 데이터 hook 추출

- `packages/cluster/frontend/src/hooks/useRoomTasks.ts` (160 lines, new):
  - `apiFetch(/api/v1/rooms/{id}/tasks)` REST + `doorae:task:updated` WS 구독 + create/update/remove 액션을 단일 hook 으로 캡슐화.
  - `roomId === null` 시 fetch suspend, WS 핸들러 등록 안 함.
  - Forward-compat 옵션으로 `goalId` 필터 인자 노출 — Phase 2 의 server-side `?goal_id=` 필터가 들어오면 그대로 동작 (현재는 server 가 무시).
  - Task 인터페이스에 `goal_id`, `triggered_by` optional 필드 미리 선언 — 마이그레이션 전 server 와도 안전한 forward compat.
- `packages/cluster/frontend/src/hooks/useRoomTasks.test.ts` (108 lines, new): 5개 contract — fetch on mount / null roomId no-op / `?status=` 쿼리 / WS 같은 룸 refetch / WS 다른 룸 무시. WS leak 방지를 위해 `afterEach(cleanup)` 명시.
- `packages/cluster/frontend/src/hooks/useRoomFiles.ts` (80 lines, new): `lib/roomFiles.ts` 의 `listRoomFiles` / `uploadRoomFile` / `deleteRoomFile` 헬퍼를 React state + idempotent refresh 핸들로 감싼 hook. delete 는 optimistic local prune (네트워크 round-trip 회피).
- `packages/cluster/frontend/src/hooks/useRoomFiles.test.ts` (78 lines, new): 3개 — fetch on mount, null suspend, optimistic delete.
- `packages/cluster/frontend/src/components/TaskPanel.tsx` (300 → 210 lines, refactor): 데이터 로직 hook 으로 이동. 행동 변화 없음 — fetch URL/WS/CRUD 모두 동일.
- `packages/cluster/frontend/src/components/RoomSharedFilesDialog.tsx` (130 → 105 lines, refactor): `useRoomFiles` 사용. 다이얼로그가 열릴 때만 명시적 refresh — 사용자가 사이드바 FilesSection 으로 같은 룸 파일을 편집했을 수 있으므로.

### Stage 3 — 새 항목 알림 (notice dot)

- `packages/cluster/frontend/src/hooks/useRightRailNotice.ts` (45 lines, new): boolean 시그널.
  - `collapsed === true` + `doorae:task:updated` 발생 → `true`
  - `collapsed → false` 전환 → `false`
  - 다른 룸 이벤트 무시
  - 카운터를 의도적으로 회피한 이유: "새"의 정의(생성? 상태변경? 기간?)가 모호해지고 토글 버튼 chrome 이 잡음으로 가득 차게 됨. dot 하나면 "볼 만한 게 있어요" 가 충분히 전달됨.
- `packages/cluster/frontend/src/hooks/useRightRailNotice.test.ts` (75 lines, new): 4개 — collapsed 시 flip on / open 시 flip 안 함 / open 전환 시 reset / 다른 룸 무시.

### Stage 4 — Right rail 컴포넌트

- `packages/cluster/frontend/src/components/RightContextRail.tsx` (95 lines, new):
  - 데스크톱: `md:static md:translate-x-0 md:w-80` ↔ `md:translate-x-full md:w-0 md:overflow-hidden md:border-l-0` (`useRightSidebarLayout().collapsed` 가 polarity 결정)
  - 모바일: `fixed inset-y-0 right-0 z-40` overlay drawer, backdrop `bg-black/25 backdrop-blur-[1px]`. `Sidebar.tsx:354-376` 패턴을 거울처럼 우측 미러.
  - ESC 키 → 모바일 drawer 닫기.
  - `roomId === null` 일 때 early-return null — 사용자가 룸을 아직 선택하지 않은 라우트에서도 안전하게 mount 가능.
  - 섹션 슬롯이 일반 stacking 이라 `<GoalsSection/>` 추가가 구조 변경 없는 한 줄 작업이 됨 — Phase 2 호환.
- `packages/cluster/frontend/src/components/right-rail/TasksSection.tsx` (155 lines, new): `useRoomTasks` 소비. 320px 좁은 폭에 맞춰 4-tab 필터를 status별 그룹 헤더로 압축. assignee picker 는 hover-truncate 라벨로 단순화 (인라인 `<select>` 는 폭 부담). inline create 입력은 assignee 없이 — `/task @agent title` 슬래시 또는 AgentSettingsDialog 가 power user 채널.
- `packages/cluster/frontend/src/components/right-rail/FilesSection.tsx` (110 lines, new): `useRoomFiles` 소비 + 기존 다이얼로그에 없던 **inline 업로드 트리거**(`<input type="file" hidden>` + `<Button>`) 추가. 삭제는 confirm 한 후 hook 의 optimistic prune 으로 즉시 반영.
- `packages/cluster/frontend/src/components/right-rail/RightRailToggle.tsx` (60 lines, new): RoomHeader 우측 슬롯에 mount. `PanelRightOpen` / `PanelRightClose` 아이콘 토글, dot 표식 (notion-blue 1.5px). 데스크톱은 `useRightSidebarLayout().toggleCollapsed` 만, 모바일은 추가로 host 의 `onMobileOpen` 콜백 호출.

### Stage 5 — RoomHeader / ChatPage 통합

- `packages/cluster/frontend/src/components/RoomHeader.tsx` (7 lines added):
  - `rightRailSlot?: ReactNode` prop 신설 — 우측 액션 그룹 끝(`RoomSettingsMenu` 다음)에 렌더.
  - 슬롯 형태로 받은 이유: `<RightRailToggle>` 이 `useRightSidebarLayout` / `useRightRailNotice` 두 hook 을 소비하는데, RoomHeader 를 hook-aware 로 만들지 않고 ChatPage 가 컨텍스트 의존성을 책임지게 함.
- `packages/cluster/frontend/src/pages/ChatPage.tsx` (140 lines edited):
  - `activeTab` state + `import TaskPanel` + `Chat / Tasks` 탭 바(line 533-564) + 분기 렌더(`activeTab === 'chat' ? <ChatArea/> : <TaskPanel/>`) 모두 제거. ChatArea 항상 mount.
  - Search 버튼은 단독 슬림 헤더 행으로 분리 — 탭 바가 사라진 자리 채움, ⌘K shortcut 가시성 유지.
  - 기존 chat surface 의 `Paperclip` "공유 파일" 버튼(line 588-596) 제거 — FilesSection 이 같은 진입점.
  - `<RightContextRail/>` 을 메인 flex column 의 형제로 mount, `roomId / participants / open / onClose` 전달.
  - `<RightRailToggle/>` 은 `<RoomHeader rightRailSlot={...}/>` 으로 주입 — 모바일 `onMobileOpen` 이 ChatPage 의 `setRightRailOpen(true)` 핸들러를 트리거.

### Stage 6 — 검증

- `cd packages/cluster/frontend && npx vitest run` → 37 files, 375 tests, 0 failures (기존 357 + 신규 18).
- `npm run build` → tsc + Vite production build, 9.04s, ts 에러 0건.
- `npx tsc --noEmit` → clean.

## Result

- **사이드바 인프라 도입** 완료. Default 접힘 + localStorage 영속 + 데스크톱 push / 모바일 drawer 양쪽 동작.
- **Tasks + Files 가 한 곳에 공존** — 탭 전환 없이 채팅 흐름과 동시에 인지. 기존 `/task` 슬래시·MCP `mark_task_status`·WS 이벤트 스트림은 변화 없음 (TaskPanel/Section 둘 다 같은 hook 을 사용하므로).
- **새 항목 dot 시그널** 동작 — 사이드바 닫힌 상태에서 다른 사용자가 task 만들면 `relative` 토글 버튼 우상단에 1.5px notion-blue 점, 펴면 즉시 사라짐.
- **Phase 2 호환 구조** 확보 — `<RightContextRail/>` 안에 `<TasksSection/>` 위로 `<GoalsSection/>` 한 줄 추가하면 자율 책임 시스템이 즉시 같은 사이드바에서 노출 가능. `useRoomTasks` 의 `goalId` 필터, `Task` 의 forward-compat 필드도 마이그레이션 전 server 호환.
- **회귀 없음** — 375/375 frontend tests green, build green. TaskPanel 동작은 hook 추출 전후 동일, 기존 `?status=` 쿼리/WS 이벤트/CRUD 모두 그대로.
- **신규 코드량 vs 가치** — 신규 9 파일 + 수정 5 파일, 약 1,300 라인 추가 / 170 라인 제거. 18 신규 테스트로 hook contracts 잠금. 다음 Phase 2 작업에서 server 마이그레이션 + Goal Scheduler/Executor 가 들어와도 frontend 표면이 흔들리지 않게 됨.

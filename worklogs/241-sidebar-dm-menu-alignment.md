# fix(ui): sidebar agent row button alignment + missing DM rename/delete menu (#241)

- PR: #241
- Date: 2026-04-22
- Branch: `fix/241-sidebar-dm-menu-alignment`

## Situation

#237이 도입한 사이드바 "Agents 섹션 + 에이전트별 DM 트리"에서 두 가지 UX 폴리시 이슈가 드러남.

1. **에이전트 행의 두 액션 버튼 높이 불일치**: `+` (새 DM) 버튼은 커스텀 `h-6 w-6` (24×24) 인라인 버튼, 바로 옆 `⋯` (AgentSettingsMenu) 트리거는 shadcn `<Button size="icon">` (`size-9` = 36×36). 같은 `<span>` 안에 나란히 놓여 수직 정렬이 어긋나 보임.

2. **에이전트 하위 DM 행에서 삭제/이름변경 수단 부재**: 프로젝트 룸은 이미 `SidebarRoomMenu` 오버플로 메뉴(Rename + Delete)를 가지지만, 에이전트 펼침 상태에서 노출되는 DM 항목은 단일 버튼만으로 렌더되어 정리할 방법이 없었음.

## Task

- AgentSettingsMenu의 기존 사용처(AdminMachines)는 그대로 유지하되, Sidebar 한정으로 24×24 트리거로 전환.
- 프로젝트 룸과 동일한 `SidebarRoomMenu` 컴포넌트를 재활용해 DM 행에도 Rename + Delete 메뉴 부착. 기존 `RoomEditDialog`를 로컬에 마운트해 rename 플로우 재사용.
- 회귀 가드로 compact variant 테스트 3건 추가.

## Action

### AgentSettingsMenu.tsx

- `AgentSettingsMenuProps`에 `compact?: boolean` 옵셔널 추가.
- `compact=true`일 때: shadcn `<Button variant="ghost" size="icon">` 대신 `<button>`을 직접 렌더. 클래스는 `SidebarRoomMenu`의 트리거와 정확히 일치하는 `h-6 w-6 rounded-[var(--radius-sm)] text-[var(--color-foreground-muted)] hover:bg-black/10 hover:text-[var(--color-foreground)]`.
- `compact=false` (기본값)은 기존 동작 유지 → AdminMachines 영향 없음.

### Sidebar.tsx — AgentDMListAdmin

- `editDMRoomId` 로컬 state + `handleDeleteDM(roomId, displayName)` 추가.
  - 삭제: `window.confirm` → `DELETE /api/v1/rooms/{id}` → `fetchAgentDMs()`로 즉시 새로고침 → 현재 선택된 DM이면 `onGo('/')`로 이동.
  - 에러 시 서버 detail을 alert로 노출. WS `room_deleted` 브로드캐스트 경유 재동기화는 유지되지만, 액션을 취한 사용자의 UI를 즉시 반영하기 위해 직접 refetch도 유지.
- DM 루프에서 단일 `<button>`을 `<div class="group">` + 내부 네비 버튼 + `<SidebarRoomMenu>` 구조로 재구성. Hover 시 메뉴 아이콘이 페이드 인 (프로젝트 룸 트리와 동일 패턴).
- `editDMRoomId`가 set되면 기존 `RoomEditDialog`를 마운트해 DM rename 처리 (PATCH `/rooms/{id}` 기존 엔드포인트 그대로 사용).
- 에이전트 행의 기존 `AgentSettingsMenu` 호출에 `compact` prop 전달.

### AgentSettingsMenu.test.tsx

- "compact variant" describe 블록 추가, 3 cases:
  - `compact` true일 때 트리거가 `<button>` 태그 + `h-6 w-6` 클래스.
  - `compact` omit 시 기본 경로가 `h-6`을 포함하지 않음 (shadcn Button 경로 유지).
  - compact 모드에서도 클릭 시 메뉴가 열림.

## Result

- **빌드**: `npm run build` green (tsc + vite).
- **테스트**: `npm test` 30 files / **327 passed** (+3 new). 회귀 없음.
- **시각적 정합성**: 두 버튼이 같은 24×24 박스로 나란히 정렬됨. DM 행 hover 시 오버플로 메뉴가 right edge에 페이드 인 — 프로젝트 룸과 동일 UX.
- **서버/프로토콜 변경 없음**. DM delete는 기존 `DELETE /rooms/{id}` 그대로, rename은 기존 `PATCH /rooms/{id}` 그대로.

## Reflections

- `AgentSettingsMenu`가 AdminMachines와 Sidebar 양쪽에서 쓰이는 상황에서 단순히 전역 크기를 줄이면 AdminMachines의 넓은 카드 레이아웃에서 역으로 너무 작아 보일 수 있었음. `compact` prop으로 호출부가 맥락을 명시하게 하는 선택이 맞았다고 판단.
- SidebarRoomMenu는 `onRename`/`onDelete` 두 핸들러만 받는 좁은 contract이라, 새 컴포넌트 없이 그대로 재활용 가능했다. 폴리시 PR에서 새 컴포넌트 도입은 과잉이었을 것.
- `handleDeleteDM`이 `ChatPage.handleDeleteRoom`과 모양은 유사하지만, DM은 `project_id`가 null이라 refetch 대상 API가 다름(`fetchAgentDMs` vs `fetchRooms(projectId)`). 원본을 공유 훅으로 빼지 않고 인라인 유지 — #257~280 블록의 주석 근거와 동일한 판단.

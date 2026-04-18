# feat(sidebar): hoist desktop collapse state into shared provider (#115)

- Commit: `1358e8f` (1358e8f60837649b900009ff3cf0e9a9591d22f2)
- Author: Changyong Um
- Date: 2026-04-18T22:41:22+09:00
- PR: #115

## Situation

#106 에서 도입한 데스크톱 사이드바 접기/펼치기가 `ChatPage` 로컬 state 에만
존재했다. 같은 `<Sidebar>` 를 렌더하는 `AdminMachinesPage` / `TopologyPage` 는
`collapsed` / `onToggleCollapsed` prop 을 넘기지 않아 헤더의 접기 버튼이 DOM
에 뜨지 않고, Ctrl/Cmd+B 단축키도 먹지 않으며, localStorage
(`doorae_sidebar_collapsed`) 에 저장된 사용자 선호도도 무시됐다. 세 페이지의
동작이 어긋난 채로 축적되면 후속 레이아웃 작업마다 같은 prop drill 을 반복해야
하는 부담이 생긴다.

## Task

- ChatPage 에만 있던 collapsed state / 토글 / localStorage 지속화 / Ctrl+B
  단축키 / 플로팅 확장 버튼을 모든 인증 페이지에서 공유 가능하게 끌어올린다.
- 기존 localStorage 키를 유지해 사용자 선호도가 보존되게 한다.
- `<Sidebar>` prop 표면을 단일 소스로 정리 (prop 과 훅 두 갈래 공존 금지).
- 기존 Sidebar / ChatPage 테스트 회귀 없이 새 훅·컴포넌트에도 단위 테스트를
  추가한다.

## Action

- `packages/cluster/frontend/src/hooks/useSidebarLayout.ts` 신규 — `RoomsProvider`
  패턴 (createContext + null-guard useContext + createElement) 그대로 복제.
  `collapsed` / `toggleCollapsed` / `setCollapsed` 공개. 지연 초기화로
  `doorae_sidebar_collapsed` 에서 하이드레이션하고 `useEffect` 로 매 변경 시
  영속화.
- `packages/cluster/frontend/src/hooks/useSidebarLayout.test.ts` 신규 — 5
  케이스: Provider 외부 호출 throw, localStorage 기본값, `'true'` 하이드레이션,
  `toggleCollapsed` 반영, `setCollapsed` 직접 반영.
- `packages/cluster/frontend/src/App.tsx:1-75` — `<RoomsProvider>` 내부에
  `<SidebarLayoutProvider>` 삽입. LoginPage / GuestInvitePage /
  GuestRoomPage 도 Provider 안으로 들어가지만 Sidebar 를 렌더하지 않아 부작용
  없음 (구독자 0 명).
- `packages/cluster/frontend/src/components/Sidebar.tsx` — `SidebarProps` 에서
  `collapsed` / `onToggleCollapsed` 삭제, 내부에서 `useSidebarLayout()` 호출로
  전환. Ctrl/Cmd+B useEffect 를 Sidebar 내부로 이동 (기존 ChatPage:142-151
  상응). 헤더 접기 버튼은 조건부 렌더 제거 후 항상 렌더 (`hidden md:inline-flex`
  로 데스크톱에서만 노출).
- `packages/cluster/frontend/src/components/Sidebar.test.tsx` — prop 기반
  `renderSidebar({ collapsed, onToggleCollapsed })` 을 `useSidebarLayout`
  모듈 모킹으로 교체. Ctrl/Cmd+B keydown → `toggleCollapsed` 호출 케이스 신규.
- `packages/cluster/frontend/src/components/SidebarExpandButton.tsx` 신규 —
  `collapsed === true` 일 때만 렌더되는 플로팅 확장 버튼 (기존
  ChatPage:416-427 JSX 이식).
- `packages/cluster/frontend/src/components/SidebarExpandButton.test.tsx` 신규
  — 3 케이스: 펼쳐진 상태엔 렌더 없음, 접힌 상태엔 렌더 + 올바른 aria-label,
  클릭 시 `toggleCollapsed` 호출.
- `packages/cluster/frontend/src/pages/ChatPage.tsx` — `sidebarCollapsed`
  state / `toggleSidebarCollapsed` / Ctrl+B useEffect / 플로팅 확장 버튼 JSX /
  `PanelLeftOpen` import 삭제. `<Sidebar>` 에 넘기던 `collapsed` /
  `onToggleCollapsed` prop 제거. `<SidebarExpandButton />` 로 교체.
- `packages/cluster/frontend/src/pages/AdminMachinesPage.tsx` 및
  `pages/TopologyPage.tsx` — `<Sidebar>` 뒤에 `<SidebarExpandButton />` 한 줄씩
  추가.

## Decisions

원본 계획 `.tmp/plan-115-sidebar-layout-shared-hook.md` 의 결정 과정을 그대로
따랐다.

- **상태 보관 장소**: Context + Provider 선택. 후보였던 모듈 전역 +
  `useSyncExternalStore` 는 프로젝트에 선례가 없고, Zustand / Jotai 도입은
  단일 boolean 에 비해 과도. `RoomsProvider` 가 이미 localStorage + Context 를
  제공하는 동일 구조라 일관성 이득이 보일러플레이트 비용을 압도.
- **Ctrl+B 핸들러 위치**: Sidebar 내부 useEffect. Provider 내부 등록은
  LoginPage 에서도 `preventDefault` 가 걸려 혼란. Sidebar 가 렌더된 페이지
  에서만 단축키가 의미를 가진다는 멘털 모델과 일치.
- **플로팅 확장 버튼 배치**: 공용 컴포넌트를 페이지별로 배치. Provider 에서
  Portal 로 자동 주입하면 페이지 레이아웃(예: Topology mobile top bar) 과 z-
  index 가 충돌할 수 있고, Sidebar 내부에 두면 접힌 상태에서 자기 자신을 위해
  외부 요소를 Portal 하는 단일 책임 위반이 생김.
- **Sidebar prop 하위 호환**: `collapsed` / `onToggleCollapsed` 완전 제거.
  훅과 prop 두 갈래 공존은 장기 유지비가 더 큰 걸로 판단. 기존 테스트는
  `vi.mock('@/hooks/useSidebarLayout', …)` 로 대체 가능.

**가정** — `doorae_sidebar_collapsed` 키를 재사용해 기존 사용자 선호가
유지된다. 모바일(`< md`) 에서는 `collapsed` 가 UI 에 영향을 주지 않고
`sidebarOpen` (off-canvas 드로어) 은 이번 범위 외로 각 페이지가 계속 개별
관리한다.

**위반 시 재검토 트리거** — Provider 에 인증 사용자 데이터를 읽는 로직이
들어가면 LoginPage 가 Provider 바깥으로 빠져야 하고, 모바일 햄버거 위치 통일
요구가 생기면 `sidebarOpen` 도 Provider 로 끌어올려야 한다.

## Result

- 프론트엔드 테스트: 21 파일 / 207 개 통과 (신규 8 개 포함:
  `useSidebarLayout.test.ts` 5 + `SidebarExpandButton.test.tsx` 3).
- `npm run build` 통과 (tsc + vite 번들링).
- 백엔드 cluster pytest 413 개 통과 — 프론트엔드 한정 리팩터라 회귀 없음 확인.
- `/` (ChatPage), `/admin/machines`, `/topology` 모두 헤더 접기 버튼 /
  Ctrl+Cmd+B / 플로팅 확장 버튼 / localStorage 지속화를 동일하게 공유.
  모바일 `< md` 오프캔버스 드로어와 독립 유지.
- 시각적 스모크 (브라우저에서 접힘 애니메이션 확인) 는 워크트리 환경 제약상
  미수행 — PR 리뷰 / 병합 후 확인 예정.

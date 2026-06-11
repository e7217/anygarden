# refactor(design-system): add spacing scale + extract shared PageShell (#435)

- Commit: `c8e2ab0` — Step 4 of #435 migration
- Author: Changyong Um
- Date: 2026-06-12
- PR: #435 (tracking issue)

## Situation

감사가 (1) `@theme`에 스페이싱 토큰이 없어 패딩이 컴포넌트마다 ad-hoc하고, (2) 페이지 셸이 통일돼 있지 않음을 지적했다. Admin 5페이지(Machines/Skills/MCP/LLMGateway/Topology)는 각자 `flex h-screen + Sidebar + SidebarExpandButton + main + 모바일 h-14 바`를 손으로 재구성했고, 모바일 바 타이틀이 `text-[15px] font-bold`로 타입 스케일을 벗어났으며 content max-width도 제각각이었다. 이는 사용자가 직접 제기한 "여백·간격·배치 통일" 목표의 핵심.

## Task

- 4px 기반 named 스페이싱 스케일 도입(`--space-1..12`), `space-6`(24px)를 카드/다이얼로그/섹션 패딩의 정규값으로
- Admin 5페이지의 셸 보일러플레이트를 공유 컴포넌트로 추출(하이브리드 결정 — Chat은 제외)
- 모바일 바 타이틀을 타입 스케일에 정합
- 회귀 없이(렌더 DOM 보존)

## Action

- `src/index.css` `@theme`: `--space-1:4px … --space-12:48px` 추가(Tailwind 번호 정렬 — 기존 `p-6`/`gap-4` 그대로 동작).
- `src/components/PageShell.tsx` 신설: `flex h-screen` + `Sidebar`(open state 소유) + `SidebarExpandButton` + `main` + 모바일 h-14 바(title prop). 타이틀 `text-sm font-semibold`로 정규화. `scroll` prop(기본 true)으로 단순 페이지는 `flex-1 overflow-auto` 자동 래핑.
- `AdminMachinesPage`/`AdminSkillsPage`/`AdminMCPTemplatesPage`: `<PageShell title=…>{component}</PageShell>`로 축약(각 34→~10줄).
- `AdminLLMGatewayPage`: 훅/Apply 로직 유지, `scroll={false}`로 secondary rail + `<Outlet>` 직접 배치.
- `TopologyPage`: `scroll={false}`로 데스크톱 헤더 + 캔버스/필터/디테일을 children으로. 미사용 `Menu`/`Sidebar`/`SidebarExpandButton` import·`sidebarOpen` state 제거.

## Decisions

- **하이브리드(채택) vs 전체 PageShell vs 값만 표준화**: 사용자 승인. Admin 5페이지는 셸이 동질적이라 추출 이득이 크고, Chat은 우측 레일·dvh·입력바 등 특수 구조라 분리하면 회귀 위험을 격리할 수 있다. 결정적 근거: Admin 페이지들의 셸이 글자까지 동일하고 max-width만 드리프트했다.
- **`scroll` prop으로 변형 수용**: LLMGateway(secondary rail)·Topology(데스크톱 헤더+캔버스)는 내부 레이아웃을 직접 소유해야 해 `scroll={false}`. 단순 페이지는 기본 스크롤 래핑. PageShell에 헤더 슬롯을 더 두는 대신 children으로 처리해 컴포넌트를 단순 유지.
- **`--space-*`를 Tailwind `--spacing-*` 네임스페이스가 아닌 일반 토큰으로**: 기존 `p-6` 유틸을 깨지 않으면서 셸/문서가 named step을 참조하도록. 제안 NOTE의 "기존 클래스 유지" 의도와 일치.
- **DOM 보존**: PageShell은 기존 JSX를 그대로 이동 — 모바일 타이틀 크기 외 렌더 결과 동일이라 런타임 회귀 위험 최소.

## Result

- `npm run build`(tsc) 통과 — 미사용 import/state가 있었다면 tsc가 잡았을 것. 셸 보일러플레이트 순 -223줄.
- 5개 full-page surface가 단일 PageShell을 경유 → rail/헤더/모바일 바가 단일 소스. 다음 드리프트를 구조적으로 차단.
- content max-width(단일 컬럼 admin = max-w-4xl)·구조적 변형(Chat/Machines two-pane/LLMGateway tabbed)은 DESIGN.md(step 6)에서 문서화 예정.

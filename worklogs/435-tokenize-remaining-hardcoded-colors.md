# refactor(design-system): tokenize remaining hardcoded colors (#435)

- Commit: `31d2acf` — Step 5 (polish, part 2) of #435 migration
- Author: Changyong Um
- Date: 2026-06-12
- PR: #435 (tracking issue)

## Situation

감사 매핑 결과 코드 곳곳에 디자인 토큰 대신 리터럴 hex(`#0075de`, `#f2f9ff`, `#f6f5f4`…)와 Tailwind 기본 팔레트(`emerald-500`, `bg-red-50`, `border-amber-200`…)가 흩어져 있었다. 특히 LLM Gateway 섹션들은 상태(running/starting/crashed/failed)·에러 배너에 Tailwind 기본색을 직접 써서 warm-neutral 시스템과 동떨어졌고, 일부 status 점(goal overdue)은 `bg-red-500` raw red였다.

## Task

- user-facing 표면(RoomHeader, AdminMachines, Topology)의 리터럴 hex를 토큰으로
- admin-llm-gateway 상태/에러 Tailwind 기본색을 시맨틱 토큰으로 일관 매핑
- step 2에서 미룬 `bg-red-500` status 점 정리
- React Flow 캔버스용 색은 건드리지 않기(렌더 깨짐 방지)

## Action

- `RoomHeader.tsx`: room-link 버튼 `#0075de`/`#0068c4` → `--color-brand`/`--color-brand-hover`.
- `AdminMachines.tsx`: 선택 카드 `#f2f9ff` → `--color-brand-tint-bg`, unplaced `#fff7ed` → `color-mix(var(--color-warning) 8%)` 틴트(기존 패턴과 정합), `border-[rgba(0,0,0,0.1|0.2)]` → `--color-border`.
- Topology DOM(`TopologyPage`/`FilterPanel`/`DetailPanel`)의 인라인 리터럴 `#f6f5f4`/`#ffffff`/`#dd5b00`/`#f2f9ff`/`#097fe8` → `surface-alt`/`surface`/`warning`/`brand-tint-bg`/`brand-tint-text`. **`topology/constants.ts`는 제외**(React Flow 캔버스 렌더링용 hex).
- `admin-llm-gateway/*`(7파일): 색상→토큰 일괄 매핑 — emerald→`success`, blue→`success-soft`(transitional), amber→`warning`, red→`destructive`. `bg-X-50[/60]`→`token/10`, `border-X-200`→`token/30`, `X-500` 점/텍스트→solid token.
- `GoalsPanel.tsx`·`GoalsSection.tsx`: status 점 `bg-red-500` → `--color-destructive`.

## Decisions

- **색상→토큰 매핑이 상태와 1:1**: admin-llm-gateway에서 emerald/blue/amber/red가 각각 running/starting/crashed/failed에만 쓰여(grep 확인), 색상 기반 일괄 치환이 곧 상태별 시맨틱 치환과 동일 → 결정적 perl로 일관 처리(에이전트 병렬 편집보다 토큰 선택 일관성이 보장됨).
- **starting → success-soft(teal)**: 원래 blue였으나 DESIGN.md가 brand-blue를 "interactive intent" 전용으로 예약하므로 status에 blue 금지. 팔레트에서 비-blue·비-경보 색인 teal을 "becoming healthy"로 매핑. 대안(gray=비활성처럼 보임, brand-blue=규칙 위반) 기각.
- **`topology/constants.ts` 미변경**: 이 hex들은 React Flow가 SVG/canvas로 렌더하는 노드 색이라 CSS var가 안정적으로 cascade되지 않을 수 있어 의도적으로 hex 유지. 감사가 지적한 것은 패널의 인라인 리터럴이므로 그쪽만 변환.
- **배경 틴트는 토큰 `/10`**: 새 `--color-*-bg` 토큰을 신설하는 대신 기존 badge destructive 패턴(`bg-[var(--color-destructive)]/10`)과 동일하게 opacity로 처리해 토큰 증식 방지.

## Result

- `npm run build` 통과. 전체 vitest 435개(47파일) 통과 — 마이그레이션 전체 무회귀.
- raw Tailwind status 색상·user-facing 리터럴 hex 제거 확인(grep). 색상 일관성(감사 핵심 목표) 달성. 캔버스 색은 안전하게 보존.

# fix(ui): align agent row action buttons side-by-side + name tooltip + hover-hide count badge (#243)

- PR: #243
- Date: 2026-04-23
- Branch: `fix/243-sidebar-agent-row-layout`

## Situation

#241에서 `AgentSettingsMenu`에 `compact` prop을 추가해 `+` 버튼과 크기를 맞췄으나, 사용자가 사이드바를 호버하면 두 버튼이 **세로로 쌓여** 에이전트 행 높이가 실질적으로 2배로 커지는 레이아웃 문제가 드러났다. 원인은 두 가지:

1. 부모 `<span>`이 일반 inline이라 자식 배치를 강제하지 않음
2. `AgentSettingsMenu`의 wrapper가 `<div className="relative">` (블록 레벨) → inline 컨테이너 안에서 새 줄로 떨어짐 (HTML block-in-inline 동작)

동시에, 사용자가 "에이전트 이름이 길어질 경우"를 문의. 이름 span에는 `truncate`만 걸려 있어 말줄임은 처리되지만 **전체 이름을 확인할 툴팁이 없고**, 호버 시 액션 버튼이 등장하면서 이름 공간이 추가로 좁아지는 2차 문제도 존재.

## Task

Plan A (plan-243)로 세 가지를 한 PR에 묶어 처리:

1. 부모 `<span>` → `inline-flex items-center gap-0.5` → `+` / `⋯` 옆으로 나란히
2. 이름 span에 `title={agent.name}` → 브라우저 네이티브 툴팁
3. count badge에 `group-hover:hidden` → 호버 중엔 숨겨 이름 공간 회복

셋 모두 서로 독립이지만, 세로 쌓임 + 긴 이름 경합의 **진짜 해결은 셋이 함께일 때 완성**되므로 한 PR 유지. 서버/프로토콜/DB 무변경, `Sidebar.tsx` 한 파일 7 라인 내 변경.

## Action

`packages/cluster/frontend/src/components/Sidebar.tsx`의 `AgentDMListAdmin` 안 에이전트 행 렌더(현재 line ~1153-1163):

### 1. 부모 span flex 전환

```diff
 <span
-  className="mr-1 shrink-0 opacity-0 group-hover:opacity-100 has-[[aria-expanded=true]]:opacity-100 transition-opacity"
+  // #243 — inline-flex is load-bearing: block-in-inline quirk
+  className="mr-1 inline-flex shrink-0 items-center gap-0.5 opacity-0 group-hover:opacity-100 has-[[aria-expanded=true]]:opacity-100 transition-opacity"
   data-testid={`sidebar-agent-actions-${agent.id}`}
 >
```

`mr-1 shrink-0 opacity-*` 원본 클래스는 모두 유지. `inline-flex items-center gap-0.5`만 추가되어 자식들이 수직 가운데 + 수평 배치 + 2px 간격.

### 2. 이름 tooltip

```diff
-<span className="truncate">{agent.name}</span>
+<span className="truncate" title={agent.name}>
+  {agent.name}
+</span>
```

`title` 속성은 브라우저 네이티브 tooltip을 요청. 접근성 보조기기가 tooltip을 읽어주므로 aria-label 중복 필요 없음.

### 3. 호버 시 count badge 숨김

```diff
-<span className="ml-auto shrink-0 rounded-full bg-black/5 px-1.5 text-[11px] text-[var(--color-foreground-muted)]">
+<span className="ml-auto shrink-0 rounded-full bg-black/5 px-1.5 text-[11px] text-[var(--color-foreground-muted)] group-hover:hidden">
   {agentDms.length}
 </span>
```

행 전체가 이미 `group`으로 설정되어 있어서 동일 트리거(호버) 공유. 호버 진입 → badge 사라지고 액션 버튼 페이드 인 (기존 `group-hover:opacity-100`), 호버 이탈 → 원위치.

## Result

- `npm run build` (tsc -b + vite): **green**
- `npm test` 전체: **30 files / 327 passed** (변화 없음, 회귀 없음)
- ruff, 백엔드 영향 없음 — 프론트엔드 단독 변경

### 시각 효과 (수동 확인 필요)

- 평상시: `에이전트 이름 ... 3` (count badge 노출)
- 호버 시: `에이전트 이름 ... + ⋯` (badge 사라지고 액션 버튼 노출, 옆으로 나란히 24×24)
- 긴 이름 마우스 정지: 브라우저 네이티브 툴팁으로 전체 이름 표시

## Reflections

- **결정 — span 유지**: `<div>`로 교체하면 flex 컨테이너로서 더 일반적이지만 HTML 시맨틱 변경 + diff 커짐. `inline-flex`는 display 한 속성만 바꿔서 의도를 고정. 최소 diff 원칙에 맞음.
- **결정 — 네이티브 `title`**: Radix Tooltip 도입이 디자인 톤엔 더 어울리지만 의존성 추가 + 기존 opacity/transition 애니메이션 충돌 리스크. 첫 패스로는 네이티브 `title`이 0 비용 + 접근성 동등. 터치 디바이스 대응은 follow-up (#243 밖).
- **결정 — 호버 시 badge 숨김**: 호버 = 액션 의도. 그 시점엔 DM 개수보다 어느 에이전트인지 확인이 우선 → 정보 우선순위에 따른 토글이 맞음. 마우스가 빠지면 즉시 복귀하므로 손실 없음.
- **follow-up 후보**: 매우 긴 이름(>30자) 대비 중간 말줄임 컴포넌트, 우클릭 컨텍스트 메뉴, Radix Tooltip 전환, AdminMachines 에이전트 행 일관성 점검 — 모두 별도 이슈로 분리 가능.

# fix(mcp-templates): restore horizontal focus ring on input focus (#132)

- Commit: `eb70d19` (eb70d190dbb1554d7c3805a1d46c6be969463f8c)
- Author: Changyong Um
- Date: 2026-04-20T19:30:36+09:00
- PR: #132 (issue — re-fix; original PR was #135)

## Situation

`/admin/mcp-templates → New MCP template` 다이얼로그에서 입력창을 포커스하면 좌우 포커스 링이 여전히 잘려 보이는 문제가 사용자에 의해 재보고되었다. 이 버그는 원래 이슈 #132 → PR #135(커밋 `8842e09`)에서 "수정 완료"된 것으로 간주되었으나, 그 수정(`overflow-auto` → `overflow-y-auto`)은 CSS spec을 오해한 결과라 실제로는 효과가 없었다. 현재 코드는 이미 `overflow-y-auto`가 적용되어 있음에도 `Input`의 `focus-visible:ring-2`(요소 바깥쪽 2px box-shadow)가 스크롤 컨테이너 경계에서 좌우 클리핑되고 있었다.

## Task

- 이전 PR #135의 가정 — "`overflow-y: auto` 단독이면 가로 링이 보존된다" — 이 왜 틀렸는지 확인하고 근본 원인 해결
- `CustomEditorDialog` 스크롤 컨테이너(`AdminMCPTemplates.tsx:756`)에서 수평 방향 포커스 링이 4면 모두 온전히 렌더링되도록 CSS 조정
- 전역 `Input` 컴포넌트 시각 표현은 바꾸지 않기 (다른 페이지 영향 최소화)
- 다이얼로그 본문 폭 손실은 인지 불가 수준으로 유지
- 재발 방지를 위해 커밋 본문에 CSS spec 근거 명시

## Action

- `packages/cluster/frontend/src/components/AdminMCPTemplates.tsx:756`
  - 스크롤 컨테이너 `<div className="space-y-3 py-2 max-h-[60vh] overflow-y-auto">`에 `px-1` 추가
  - 결과: `<div className="space-y-3 py-2 px-1 max-h-[60vh] overflow-y-auto">`
- 격리 HTML 테스트(`/tmp/focus-ring-test.html`) + Playwright로 CSS 동작 검증
  - `overflow-y-auto`만 적용: 링 좌우 클리핑 재현됨
  - `overflow-y-auto + px-1`: 4면 링 온전 표시 확인
- 타입체크 + 빌드: `cd packages/cluster/frontend && npm run build` 통과 (12344 modules)
- 커밋 본문에 CSS Overflow Module Level 3 근거 명시해 후속 작업자가 같은 오해를 반복하지 않도록 기록

## Decisions

`.tmp/plan-132b-focus-ring-clip-refix.md`에서 4개 대안을 비교:

| 대안 | 결과 |
|------|------|
| **A. `px-1`(4px 좌우 padding) 추가** | **선택** — 구조 유지, 1줄 수정, 모든 자식 Input에 자동 적용 |
| B. `overflow-visible` + 외부 래퍼로 스크롤 이동 | rejected — 마크업 리팩터 비용이 1줄 수정 대비 과함. `max-h` 계산 레이어 이동 필요하고 `DialogContent`의 grid auto-sizing과 간섭 위험 |
| C. Input에 `focus-visible:ring-inset` | rejected — 링이 요소 안쪽으로 들어가 DESIGN.md 포커스 표현 일관성 훼손. 전역 Input 수정하면 영향 크고, 다이얼로그만 로컬 패치하면 시각 일관성 깨짐 |
| D. Input에 `w-[calc(100%-4px)] mx-auto` | rejected — 모든 Input에 반복 적용 필요. DRY 위반, 유지보수 부담 |

**결정적 근거**: CSS Overflow Module Level 3상 `overflow-y: auto`가 설정되면 `overflow-x`의 used value도 `auto`로 변환되어 가로 클리핑이 발생한다. 따라서 링을 보존하려면 (1) 링을 안쪽으로 그리거나 (2) 바깥쪽에 그릴 공간을 확보해야 하는데, (1)은 전역 시각 디자인 영향이 크므로 (2)를 택함. (2)의 구현 중 `px-1`이 가장 지역화된 변경. 손실되는 본문 폭은 `max-w-2xl`(672px) 기준 ~0.6%로 육안 인지 어려움.

**이전 시도와의 차이**: PR #135(`8842e09`)는 `overflow-auto` → `overflow-y-auto` 변경을 "수직 스크롤만 필요하므로 수평 링이 자연스럽게 보존된다"는 가정으로 적용했으나, CSS spec상 두 축의 동작 원리가 같다(한 축이 non-visible이면 다른 축의 used value도 auto). 이번 수정은 링이 그려질 공간을 별도로 확보하는 것으로 근본 해결.

**가정**: 현재 `ring-2`(2px)가 디자인 시스템 표준 포커스 링 두께라는 전제. 향후 링 두께 변경 시 padding 값 재검토 필요.

**미해결/후속**: 다른 admin 다이얼로그(`AdminMachines.tsx`의 `overflow-y-auto` 스크롤 컨테이너 등)에도 동일 패턴이 있을 수 있으나 실제 포커스 링 잘림이 보고된 것만 대응. 전역 Input 또는 디자인 시스템 레벨에서 재발 방지 가이드 추가는 이번 범위 외로 남김.

## Result

- `/admin/mcp-templates → New MCP template` 다이얼로그의 모든 Input(Display name, Slug, Description, Command, Args, Env key/value, Advanced TOML)이 포커스 시 4면 모두 파란 포커스 링 표시
- 다이얼로그 본문 폭 4px 감소 (672px → 668px content-box). 육안 인지 불가
- 세로 스크롤 동작 유지 (Advanced 모드 긴 TOML도 정상 스크롤)
- `AdminSkills.tsx` 등 이전 PR에서 함께 수정된 다른 변경은 보존 (이번 커밋은 MCP templates scope만 터치)
- Playwright 격리 테스트로 CSS 원리 검증 완료. 실제 앱에서의 수동 검증은 dev 서버가 worktree 기준으로 뜨면 가능

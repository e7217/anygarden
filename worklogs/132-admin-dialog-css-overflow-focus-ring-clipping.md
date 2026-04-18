# fix(cluster): admin dialog CSS overflow + focus ring clipping (#132)

- Commit: `808c6b0` (808c6b041510a2d7acd338fb0c53199be666eeeb)
- Author: Changyong Um
- Date: 2026-04-19T04:18:10+09:00
- PR: #132 (issue)

## Situation

Admin UI에서 두 다이얼로그가 시각적으로 깨지고 있었다. (1) AdminSkills Preview에서 `SKILL.md` 블록이 다이얼로그 너비(672px)를 넘어 좌우로 삐져나갔고, (2) AdminMCPTemplates "New template"에서 입력에 포커스 시 좌/우 포커스 링이 사라지고 상/하 파란 선만 보였다. Playwright로 확인한 결과 전자는 `<pre>`의 scrollWidth가 1044px로 측정되어 다이얼로그 밖으로 374px 이상 확장, 후자는 부모 컨테이너 `overflow-auto`가 자식 input의 focus-visible ring을 수평 방향에서 클리핑하고 있었다.

## Task

- `DialogContent`가 `display: grid`라 자식이 `min-width: auto`로 intrinsic 크기로 확장되는 것을 막아 `<pre>`와 `<ul>`이 다이얼로그 안에 들어오도록 제약
- 내부 `overflow-auto`가 가로 스크롤바를 뱉도록 유지 (원본 라인 구조 보존이 Preview 목적에 부합)
- "New template" 모달의 body 컨테이너가 세로 스크롤만 필요함을 반영해 수평 방향 클리핑 제거
- 서버·DB·API 변경 없이 CSS 클래스만으로 해결

## Action

- `packages/cluster/frontend/src/components/AdminSkills.tsx:703-724`
  - `preview` 본문 래퍼 `<div className="space-y-3">` → `<div className="space-y-3 min-w-0">`
  - SKILL.md 래퍼 `<div>` → `<div className="min-w-0">`
  - `<pre>` 클래스에 `max-w-full` 추가
  - 보조 파일 래퍼 `<div>` → `<div className="min-w-0">`, `<ul>`에 `max-w-full` 추가
- `packages/cluster/frontend/src/components/AdminMCPTemplates.tsx:573`
  - 모달 body 컨테이너 `overflow-auto` → `overflow-y-auto`
- 타입체크 + 빌드 검증: `cd packages/cluster/frontend && npm run build` 통과 (12340 modules)

## Decisions

`.tmp/plan-132-admin-dialog-css-fixes.md`에서 결정된 선택지:

**AdminSkills Preview 오버플로우 처리**
- A. `<pre>`에 `whitespace-pre-wrap break-words` — 스크롤 불필요하지만 코드 라인 구조가 깨짐 → **rejected**
- B. `min-w-0` + `max-w-full` + 기존 `overflow-auto` 유지 — 가로 스크롤바로 원본 라인 보존 → **선택**
- C. `whitespace-pre-wrap`만 추가 — A와 동일 문제 → **rejected**

결정적 근거: Preview 목적이 "원본 스킬 정의 확인"이라 줄 구조 보존이 가독성에 중요. 기술 사용자(admin) 대상이라 가로 스크롤 UX 수용 가능. 기존 `max-h-72 overflow-auto` 세로 스크롤과 일관.

**"New template" focus ring 클리핑**
- A. `overflow-auto` → `overflow-y-auto` — 의도(세로 스크롤 only)에 정확히 맞음 → **선택**
- B. `px-1` 패딩 추가 — 가로 공간 2px 손실. 근본 해결 아님 → **rejected**
- C. `focus-visible:ring-inset` — ring이 input 안쪽으로 들어와 시각적 축소 → **rejected**

결정적 근거: 이 모달은 수직 스크롤만 필요한 폼이라 `overflow-y-auto`가 의도와 1:1 매칭. 수평 방향 자유도 확보로 포커스 링이 자연스럽게 보존됨.

가정: Preview `<pre>`의 내용이 코드 블록 성격이라 줄바꿈 개행보다 가로 스크롤이 더 적합하다는 판단. 향후 non-code 콘텐츠를 Preview에 넣게 되면 이 결정을 재검토해야 함.

## Result

- AdminSkills Preview의 SKILL.md · 보조 파일 목록이 다이얼로그 안에 들어옴. `<pre>`·`<ul>` 내부 가로 스크롤바로 긴 라인 탐색 가능
- AdminMCPTemplates New template의 "Name (slug)" 입력에 포커스 시 4면 모두 파란 포커스 링 정상 표시
- 빌드 경고 없음, 타입체크 통과
- 단위 테스트 회귀 없음 (CSS 클래스만 변경, 관련 admin 테스트 파일 부재 확인)

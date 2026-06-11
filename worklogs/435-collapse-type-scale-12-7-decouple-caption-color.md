# refactor(design-system): collapse type scale 12→7, decouple caption color (#435)

- Commit: `9b47a1a` (9b47a1a... — Step 1 of #435 migration)
- Author: Changyong Um
- Date: 2026-06-12
- PR: #435 (tracking issue)

## Situation

Claude Design 핸드오프 감사가 타이포그래피에서 **평행 스케일 2개**를 지적했다: element 스타일(h1 32 / h2 24 / h3 18)과 9개 size 유틸리티가 공존하며 큰 쪽은 40/48/64로, 중간은 20/22/26으로 클러스터링되어 겹치고 비어 있었다. 12개 토큰은 단일 제품에 과하고, `.text-caption`은 크기 유틸이면서 색상(muted)까지 굽고 있어 크기와 색상이 결합되어 있었다.

## Task

- 12개 타입 토큰을 ~7단계 단일 스케일로 축소 (display 48 / title 32 / heading 24 / lead 20 / body 16 / caption 14 / badge 12)
- `text-section`/`text-display-sm`/`text-subheading`/`text-card-title`/`text-body-lg`/`text-nav` 제거
- h1→title, h2→heading, h3→lead element 스타일 매핑
- size 유틸에서 색상 분리 (`.text-caption`의 baked-in muted 제거)
- 기존 사용처를 새 스케일로 마이그레이션하되 시각 회귀 없이

## Action

- `src/index.css`: `@utility` 타입 블록(9개)을 6개(+body 기본=7단계)로 교체. `text-display` 64→48px. `text-title`/`text-heading`/`text-lead` 신설. `text-caption`에서 `color` 라인 제거. element 스타일 h3 18→20px(lead 정합), h1/h2 letter-spacing 정리.
- 사용처 마이그레이션: `LoginForm.tsx`(display-sm→title, body-lg→lead), `ChatArea.tsx`/`ChatPage.tsx`(body-lg→lead), `RoomHeader.tsx`/`ui/card.tsx`/`ui/dialog.tsx`(card-title→heading), `Sidebar.tsx`(nav→`text-sm font-medium`).
- 색상 분리에 따라 색상이 없던 8개 `text-caption` 사용처(ChatPage, ChatArea, LoginPage, RoomHeader×2, GuestRoomPage, Sidebar)에 명시적 `text-[var(--color-foreground-muted)]` 추가해 외형 보존.

## Decisions

- **`text-caption`(14)/`text-badge`(12) 이름 유지 vs 제안의 label/caption 리네임**: 제안 문서는 14=label, 12=caption으로 명명했으나, 기존 코드는 `text-caption`(14, ~40곳)·`text-badge`(12, ~17곳)를 일관 사용 중이었다. 강제 리네임 시 57곳 수정 + 텍스트 크기 축소 회귀가 발생한다. 감사의 **측정 가능한 목표**(6개 중복 유틸 제거, ~7단계 축소, 색상 분리)는 기존 이름을 유지해도 모두 달성된다 → 기존 이름 유지, 매핑은 DESIGN.md에 문서화 예정.
- **색상 분리 채택**: LOW 심각도지만 제안이 명시. 거의 모든 `text-caption` 사용처가 이미 명시적 색상을 동반하므로 분리가 안전했고, 색상 없는 8곳만 grep으로 특정해 muted를 보강했다.
- **가정**: `text-display`를 쓰는 표면이 1곳뿐이라 64→48 축소 영향이 작다. 틀어지면(향후 display 다용) 재검토.

## Result

- `npm run build`(tsc 포함) 통과. RoomHeader/Sidebar/MessageBubble vitest 39개 통과. 제거된 유틸리티를 참조하는 테스트 없음.
- 타입 토큰 9→6 유틸(+body=7단계), 색상·크기 분리 완료. 후속 단계(danger 분리, 톤 토큰화, 스페이싱/셸, 폴리시)의 기반.

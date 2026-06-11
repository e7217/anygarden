# refactor(design-system): split danger from warning, unify destructive reds (#435)

- Commit: `c5d7049` (+ code in `7da54a9`) — Step 2 of #435 migration
- Author: Changyong Um
- Date: 2026-06-12
- PR: #435 (tracking issue)

## Situation

감사가 `--color-warning` = `--color-danger` = `--color-destructive`가 모두 하나의 오렌지(`#dd5b00`)로 수렴함을 지적했다 — "delete forever"와 "heads-up"이 동일하게 보였다. 또한 button/badge의 `destructive` variant가 `--color-warning`를 직접 가리키고 있었고(이름과 의미 불일치), 코드 곳곳의 삭제·에러 표시가 raw Tailwind red(`text-red-600`, `text-red-400`, `hover:bg-red-50` 등)를 제각각 사용해 hover/색상 전략이 3가지로 갈렸다. 일부 코드는 이미 `var(--color-destructive,#d74c4c)` 폴백으로 빨강을 기대했지만 토큰은 오렌지로 해석됐다.

## Task

- destructive 액션 전용 red를 warning(caution)에서 분리
- shadcn 레거시 클래스(`--color-destructive`)가 깨지지 않도록 별칭 유지
- button/badge `destructive` variant를 의미에 맞는 토큰으로 재지정
- destructive 액션 + error 텍스트의 raw Tailwind red를 토큰으로 통일 (status 점·admin-llm-gateway 배너는 step 5로)

## Action

- `src/index.css`: `--color-danger` `#dd5b00` → `#c83a2b`(별도 벽돌빛 red), `--color-warning` `#dd5b00` 유지(caution), `--color-destructive: var(--color-danger)`(shadcn 별칭).
- `ui/button.tsx`·`ui/badge.tsx`: `destructive` variant를 `--color-warning` → `--color-destructive`.
- 15개 컴포넌트(SidebarRoomMenu, SidebarProjectMenu, RoomSettingsMenu, AgentSettingsMenu, ParticipantListPopover, AdminMachines, TaskPanel, GoalForm, RoomArtifactsDialog, RoomSharedFilesDialog, MessageInput, right-rail/{TasksSection,FilesSection,GoalsSection}, agent-settings/GoalsPanel)의 삭제 버튼·아이콘·에러 텍스트 raw red(`text-red-*`/`hover:bg-red-50`/`border-red-*`)를 `--color-destructive` 토큰으로 통일.
- `AgentSettingsMenu.test.tsx`: delete 버튼 단언을 `text-red-600` → `text-[var(--color-destructive)]`로 갱신(의도=destructive 스타일링 유지).

## Decisions

- **별칭 유지(`--color-destructive: var(--color-danger)`)**: 직접 `#c83a2b`로 바꾸지 않고 danger를 별칭. shadcn 레거시 클래스가 그대로 해석되어 색상 분리만으로 컴포넌트 churn이 없다(제안 문서의 NOTE 근거).
- **danger 값 `#c83a2b`**: 제안의 "출발점" 값 채택. warm-neutral과 어울리는 벽돌빛이며, 기존 `#d74c4c` 폴백과 근사해 폴백 사용처도 정합.
- **raw red 통일 범위**: destructive 액션 + error 텍스트만 토큰화(ultracode 원칙 — 일관성). `bg-red-500` status 점(GoalsPanel/GoalsSection 등)과 admin-llm-gateway 에러 배너(`bg-red-50`/`border-red-200`)는 status/배경 토큰이 별도로 필요하므로 step 5로 분리. 결정적 find-replace이므로 워크플로 대신 정밀 perl로 일관 처리.
- **disconnected 뱃지가 빨강으로**: RoomHeader/GuestRoomPage의 `Badge variant="destructive"`가 이제 빨강. disconnected는 error 성격이라 적절 — DESIGN.md §2 status 원칙은 step 6에서 정합되게 갱신 예정.

## Result

- `npm run build`(tsc) 통과. AgentSettingsMenu 등 vitest 통과. 처리 대상에서 `bg-red-500` status 점 2개만 의도적으로 남김.
- warning(오렌지 caution)과 danger(빨강 destructive)가 분리되어 "삭제"와 "주의"가 시각적으로 구분됨. 삭제·에러 색상이 단일 토큰으로 통일.
- 부수: node_modules symlink를 공용 git exclude에 추가해 커밋 오염 방지.

# feat(rooms): right rail viewport-driven default + width staging (#329)

- Commit: `409f4a2` (409f4a2805f6f034c4e6bcaaa0e463e579d3b192)
- Author: Changyong Um
- Date: 2026-04-30T02:39:38+09:00
- PR: #329 (issue)

## Situation

The right context rail was hard-coded to 384px (`w-96`) and started collapsed regardless of viewport. Below the `xl` breakpoint this squeezed the chat canvas, and the recent rail PRs (#322, #324, #326, #328) had been patching downstream symptoms — assignee slot widening, hover truncation, action-backdrop opacity — rather than the upstream "rail eats too much width" problem. Issue #329 traced these patches back to the fixed-width + single-`md:` breakpoint pair.

## Task

- Make the no-`localStorage` default viewport-aware: stay collapsed on narrow screens, start expanded on `lg+` where there is room for the rail and the conversation.
- Preserve the existing persisted-preference contract — users who already toggled the rail must not see a surprise reset.
- Stage the rail width across breakpoints so `xl` keeps the original 384px, `lg` gets 320px, and everything below uses 288px (mobile drawer included).
- Add tests covering both viewport branches and the storage-beats-viewport rule.

## Action

- `packages/cluster/frontend/src/hooks/useRightSidebarLayout.ts`
  - Introduced `LG_BREAKPOINT_QUERY = '(min-width: 1024px)'`.
  - Rewrote `readInitial` so it returns the persisted value when present and otherwise consults `window.matchMedia(LG_BREAKPOINT_QUERY)`. Falls back to `true` (collapsed) when `matchMedia` is unavailable.
  - Replaced the "default *collapsed*" header comment with the new viewport-driven contract referencing #329.
- `packages/cluster/frontend/src/hooks/useRightSidebarLayout.test.ts`
  - Added `mockMatchMedia(matches)` helper using `Object.defineProperty(window, 'matchMedia', …)` since jsdom does not implement it.
  - `beforeEach` defaults the mock to `matches=true` (lg viewport) so unrelated tests have a stable answer.
  - Replaced the single `defaults collapsed=true` test with two: one for `lg+` (`collapsed=false`) and one for sub-lg (`collapsed=true`).
  - Updated the hydrate tests to assert that an explicit storage value wins even when the viewport policy would say otherwise.
  - Pinned the `toggleCollapsed` test to a sub-lg viewport so its assertions still flow from the original-default starting state.
- `packages/cluster/frontend/src/components/RightContextRail.tsx`
  - Changed the base width from `w-96` to `w-72` and the desktop expanded class from `md:w-96` to `md:w-72 lg:w-80 xl:w-96`.
  - Added a `#329` comment block explaining the staging.

## Decisions

The `.tmp/plan-329-frontend-responsive-layout.md` plan and the issue body weighed three rail-handling options:

- **A1** — pure breakpoint width staging without a default-policy change. Rejected: the rail still starts open at every viewport, so users on a 900px laptop still see the same squeezing on first visit.
- **A2** — fluid width via flex sizing. Rejected for this PR: every child slot in `TasksSection`/`GoalsSection` (e.g. `w-[8rem]` assignee slot from #324, the `max-w-[240px]` file chip in `MessageBubble.tsx:71`) is independently fixed-width, so making the rail itself fluid without re-tuning the slots would just push truncation back inside the rail.
- **A3 (chosen)** — sub-lg auto-collapse + breakpoint width staging, leveraging the existing `useRightSidebarLayout` + `useRightRailNotice` + `RightRailToggle` infrastructure.

A3 won because all of `localStorage` persistence, the toggle button in `RoomHeader`, and the unread-context dot already exist; the change collapses to a single branch in `readInitial` plus three Tailwind tokens. The decisive observation was that the inverted-polarity comment ("default *collapsed*") in the old `readInitial` predates the right-rail toggle in `RoomHeader` — once the toggle landed, the "always collapsed" default was an unforced loss.

Assumptions to revisit if violated:

- Persisted-preference precedence is the right contract. If users complain that the auto-default should win on every visit, this needs revisiting (the storage write happens on first toggle, so it sticks fast).
- 1024px is the right cutoff. The plan picked it because the chat canvas has roughly enough room at that width for the sidebar (≈64px) + 320px rail + readable conversation. If the sidebar grows or the chat canvas needs more width, the cutoff might need to move to `xl` (1280px).
- The child slots inside the rail will not break at `w-72` (288px). The recent #322/#324/#326/#328 work targeted the previous `w-96`; if anything truncates at 288px, that becomes Phase 4 follow-up rather than a blocker here.

Phase 2 (agent message `max-w`), Phase 3 (legacy entry-point cleanup), and Phase 4 (mobile breakpoint sweep) from the plan are deliberately deferred to separate PRs.

## Result

- 381/381 frontend tests pass; `npm run build` succeeds (existing chunk-size warning unchanged).
- New behavior: a fresh visitor at ≥1024px sees the rail open by default; below 1024px sees it collapsed, with the existing toggle and notice-dot still functioning.
- Existing users with a `doorae_right_sidebar_collapsed` value in `localStorage` are unaffected.
- Rail no longer takes 384px below the `xl` breakpoint, so the chat canvas reclaims 64–96px in the 1024–1279px range.
- Pending follow-ups (separate PRs per plan): agent-message `max-w`, legacy chat-page entry-point row removal, breakpoint sweep + mobile verification at 320/480/768/1024/1440px.

# refactor(rooms): hide header search below sm + add menu fallback (#329)

- Commit: `933f57c`
- Author: Changyong Um
- Date: 2026-04-30 (Phase 4 of #329)
- PR: pending (issue #329)

## Situation

Phase 3 (#332) absorbed the search trigger into `RoomHeader` as a small icon button between the agent-liveness badge and the settings menu. That worked at desktop widths, but at 320px (the smallest target viewport in the plan's matrix) the right-side strip now had to seat: participants + connected dot + agent-liveness + search + settings + right-rail-toggle alongside the room name. The room name was the loser.

Static analysis was enough to call this out without a manual sweep — the rest of the 320/480/768/1024/1440 verification still needs to happen in a browser.

## Task

- Hide the direct search icon below `sm` so the header strip is uncluttered on phones.
- Provide a non-keyboard fallback for mobile users (who can't type ⌘K). Pushing a "Search messages" entry into the existing overflow menu is the cheapest path that keeps search reachable on every device.
- Not regress desktop behaviour — at `sm`+ the direct icon button stays exactly where Phase 3 placed it.

## Action

- `packages/cluster/frontend/src/components/RoomHeader.tsx`
  - Search icon button gains `hidden sm:inline-flex` (was `inline-flex`). Added a comment block explaining the intent and pointing at the menu fallback.
  - `RoomSettingsMenu` invocation now receives `onSearch={onSearch}` so the same handler that drives the icon also drives the menu entry.
- `packages/cluster/frontend/src/components/RoomSettingsMenu.tsx`
  - Imported `Search` lucide icon.
  - Added `onSearch?: () => void` to the props interface, with JSDoc explaining the mobile-fallback rationale.
  - Inserted the "Search messages" entry at the top of `safeActions` so it lands above "Create sub-room" — searching is more glance-frequent than the room-management actions further down.

## Decisions

The plan (`.tmp/plan-329-frontend-responsive-layout.md` Phase 4) called for a "320/480/768/1024/1440 viewport sweep + breakpoint sweep boosting" round. The honest reading is that most of that round needs visual verification in a real browser; the remaining work for the AI agent is to land any breakpoint gaps that are unambiguous from static analysis.

The Phase 3 search-icon promotion was the one such gap: with it added unconditionally, a 320px column couldn't fit both the strip and the room name. Three options:

- **D1 (chosen)** — hide direct icon below `sm`, add menu fallback. Mobile retains a path; desktop is unchanged.
- **D2** — hide it below `md` (768px). Rejected: that pushes search out of the header on the entire tablet range too, and Phase 1's rail policy already optimises for the canvas at `lg+`. Asking tablet users to dig into the menu for search is harsher than necessary.
- **D3** — drop the direct icon entirely, keep search only in the menu and ⌘K. Rejected: the desktop discoverability win from a visible search icon was the main reason Phase 3 chose to render it. Reverting that within one issue would be churn.

Decisive observation: `RoomSettingsMenu`'s `safeActions` contract gives a free menu entry per new prop, with no menu-visibility logic to update. So the "menu fallback" half of the change is one prop + one entry — small enough to land alongside the breakpoint tweak.

Assumptions to revisit if violated:

- `sm` (640px) is the right cutoff. The static-analysis case was 320px. If the icon is also visibly cramped at, say, 600px after manual review, the cutoff might need to move to `md`.
- The menu's "Search messages" position (top of `safeActions`) reads naturally. If usability testing shows users overlook it because it sits in an "admin actions" mental model, the alternative is a dedicated section header inside the menu — but that would be a follow-up, not part of this PR.
- The remaining viewport-sweep findings (mobile dialog overflow, `MessageInput` polish, tablet layout) come from manual verification. This PR explicitly does not chase them.

## Result

- 381/381 frontend tests pass; `npm run build` succeeds (existing chunk-size warning unchanged).
- Phones (sub-sm): search icon hidden in the header strip, "Search messages" available via the overflow menu.
- `sm`+: behaviour identical to Phase 3.
- Pending follow-ups: manual viewport sweep at 320 / 480 / 768 / 1024 / 1440px; any visual issues found there flow into separate PRs.

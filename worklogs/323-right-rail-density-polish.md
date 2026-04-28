# feat(rooms): right rail density polish — wider rail + unified assignee slot + split goals meta (#323)

- Commit: `f40384d` (f40384d2a1126d3bd67d9a9f75dd28708821bcc6)
- Author: Changyong Um
- Date: 2026-04-29T01:25:31+09:00
- PR: #323

## Situation

The Right Context Rail introduced in #302 is fixed at `md:w-80` (320px) on desktop. Inside that 320px column the `TasksSection` rows had to fit five elements on a single line — status icon, title, hover-only reassign `<select>`, always-on assignee chip, hover-only delete button — and `GoalsSection` rows packed `assignee · trigger · next 5h · 1 fail` onto a single meta line. With Korean titles and multi-segment meta this regularly truncated the title to ~5–6 characters on hover, and the goal meta line broke or clipped at the right edge. The rail "felt cut off" even though no data was actually missing.

## Task

- Reduce row density without dropping any visible information.
- Preserve every existing `data-testid` (`right-rail-task-row-*`, `right-rail-task-assignee-*`, `right-rail-goal-row-*`, `right-rail-goal-assignee-*`, `right-rail-file-row-*`) so future E2E hooks remain stable.
- Keep the chat canvas readable on a 1280px display (≥ 600px chat width).
- Stay inside the frontend — no data plane / API / WebSocket changes.

## Action

- `packages/cluster/frontend/src/components/RightContextRail.tsx:82,87` — replaced both `w-80` occurrences with `w-96` (320px → 384px). Both the mobile-overlay base class and the desktop `md:` static branch were updated; the collapsed branch (`md:w-0`) was left alone.
- `packages/cluster/frontend/src/components/right-rail/TasksSection.tsx:244-274` — collapsed the previously separate hover-only `<select>` and always-on assignee chip into a single 7rem swap slot. The chip and the select share the same box via `relative + absolute inset-0`; the chip is `aria-hidden`, becomes `invisible` on `group-hover` / `group-focus-within`, and the select fades in at the same coordinates. Unassigned tasks now render `—` at rest instead of an empty gap. The `data-testid="right-rail-task-assignee-${task.id}"` selector stays on the `<select>`.
- `packages/cluster/frontend/src/components/right-rail/GoalsSection.tsx:147-173` — split the single meta `<p>` into two truncating lines. Line 1 is the assignee name (lifted `data-testid="right-rail-goal-assignee-${g.id}"` here, with a `title=` fallback so long agent names remain accessible). Line 2 is `trigger · next 5h · N fail` with the existing conditional segments. `manual` goals keep the trigger label so the second line is never empty.
- Verified: `npm run build` succeeds; `vitest run` reports 380/380 tests passing; `grep` confirms all five `data-testid` selectors are present in the new tree.

## Decisions

Sources: `.tmp/plan-323-right-rail-density.md` (full Phase 2 design with the alternatives matrix), GitHub issue #323 body, the brainstorming exchange that triggered the issue.

Three independent design knobs were considered, and the plan deliberately turned all three at once because each one alone left the underlying problem partially intact.

- **Rail width**: weighed `w-80` (do nothing), `w-[360px]` (custom token), `w-96` (chosen, 384px), `w-[420px]` (rejected). 360px was rejected because the codebase prefers Tailwind's standard width tokens over arbitrary values, and 420px was rejected because on a 1280px monitor it pulls the chat canvas below the ~600px readability threshold. 384px is the smallest standard token that simultaneously (a) accommodates the 7rem assignee slot, (b) gives the goals meta two lines without horizontal clipping, and (c) keeps the chat ≥ 640px.
- **Tasks assignee slot**: weighed keeping both elements (chip + hover select), swapping them in the same box (chosen), showing only the select at all times, or moving to an inline editor on click. Keeping both was rejected because that's the mechanism causing the title's truncate budget to halve on hover. Always-on select was rejected because the select chrome (border, dropdown caret) reads as visual noise at rest and clashes with the rail's "feels-it" minimalism. Inline editors were rejected as scope creep. The swap pattern was decisive because it gives the title column a *constant* truncate budget — the visible width of the assignee column doesn't change between rest and hover.
- **Goals meta layout**: weighed leaving the single line and pushing the assignee to hover-only, splitting into two lines (chosen), splitting into three lines, or moving the assignee into a chip next to the title. Hover-only assignee was rejected because the rail's primary value is showing *who owns what responsibility* — that has to be visible at rest. Three lines created a dead second line for `manual`-trigger goals. A title-line chip just relocates the truncate squeeze. Two lines was decisive because (i) the natural reading order is "who → how often / when / failures", (ii) every trigger type produces a populated second line, (iii) the row-height delta (~+16px) is acceptable for the typical 1–5 goals per room.

Assumptions worth revisiting later: rooms with 20+ goals per room would shift the row-height tradeoff against the two-line layout; sub-360px viewports may need a `min(384px, calc(100vw - 3rem))` guard on the mobile drawer (not added in this PR — flagged in the plan's Risks section).

## Result

- Right context rail is 384px wide on desktop with the same collapsed/open state machine as before.
- Task rows show the assignee name (or `—`) at rest in a fixed 7rem slot; hovering or tabbing into the row swaps in the reassign dropdown without changing the title's truncate width.
- Goal rows show the assignee name on its own line above `trigger · next · failures`; long agent names truncate with a tooltip fallback rather than overflowing.
- All five existing right-rail `data-testid` selectors are preserved.
- 380/380 frontend Vitest tests pass; no Python tests touched.
- Pending: visual sign-off across 1440 / 1280 / 1024 / 360px viewports (called out in the plan's Step 5 — not blocking the commit, recommended before merging).

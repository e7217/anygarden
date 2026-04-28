# fix(rooms): right rail hover text truncation — appearance-none + wider slot + opaque action backdrop (#327)

- Commit: `a3479a8`
- Author: Changyong Um
- Date: 2026-04-29
- PR: #327 (issue) — pending

## Situation

After #326 fixed the right-edge alignment between section headers and rows, manual hover testing surfaced two residual hover-state issues. (1) The Tasks assignee `<select>` truncated 14-character agent names like `agent01-claude` into `agent01-cla...` because the 7rem slot was simultaneously hosting a native dropdown caret (~16-20px chrome the browser draws) and the 20px `pr-5` reservation that keeps the absolute delete button from crashing into the live text. The two together left only ~70-75px of usable text width, and 14 chars don't fit. (2) The Goals action cluster used `bg-[var(--color-surface-alt)]/80` (semi-transparent), so on hover the meta line under the Run/Pause/Delete buttons bled through and made the buttons hard to read.

## Task

- Make the assignee `<select>` show full agent names without truncation on hover, while keeping the delete button visible at the row's right edge.
- Make the Goals action cluster cover whatever meta text it overlays, without resorting to a full row-width replacement that would feel like a layout shift.
- Don't undo any of #325's right-edge alignment work.
- Don't touch data-testids, data flow, or any non-rail code.

## Action

- `packages/cluster/frontend/src/components/right-rail/TasksSection.tsx:252` — assignee slot width `w-[7rem]` → `w-[8rem]`. The slot still ends at the row's inner right edge (it's the last flex child), so headers remain right-aligned with the slot; only the title's `flex-1` column gives back 16px.
- `packages/cluster/frontend/src/components/right-rail/TasksSection.tsx:264` — added `appearance-none` to the `<select>` so the browser stops drawing the native dropdown caret. Combined with the wider slot, usable text width is now `128 - 20 = 108px` (vs. `~75px` before), comfortably fitting `agent01-claude` (~88-90px rendered) without `truncate` engaging.
- `packages/cluster/frontend/src/components/right-rail/GoalsSection.tsx:181` — action cluster `bg-[var(--color-surface-alt)]/80` → solid `bg-[var(--color-surface-alt)]`, plus `shadow-sm` for a faint lift so the cluster reads as floating over the row rather than glued. The hover row already paints the same surface token, so the cluster blends seamlessly with the rest of the row's background.
- Verified at 1440×900 in Playwright with the worktree dev server (port 5174), forcing `:hover` via an injected stylesheet. Post-fix `selectedText` reads `agent01-claude` in full, `appearance` is `none`, slot width is 128px.

## Decisions

Sources mined: issue #327 body, PR #324 / #326 descriptions, the brainstorming exchange where the user reported "글씨들이 잘림".

Three options were weighed for the select truncation:

- **Drop `pr-5` and let the trash icon overlay the select's right edge** — would have given the select more text room, but the trash icon would land on top of native chrome (caret) on browsers that still render it without `appearance-none`, producing inconsistent visual collisions. Rejected because the fix would be browser-dependent.
- **Add `appearance-none` only, keep the 7rem slot** — usable width becomes `~92px`, marginally fitting `agent01-claude` (~88-90px). Rejected because it's tight; one more character (`agent01-codex` is borderline, `agent01-codex-extra` would not fit at all) and we're back to truncation.
- **Add `appearance-none` AND widen the slot to 8rem (chosen)** — `108px` usable width is comfortable for the current name set (`agent01-claude`, `agent01-codex`, `agent01-gemini`, `test-agent`, all ≤ 14 chars) and leaves headroom for slightly longer names. The 16px the slot took away from the title's `flex-1` column is acceptable: at 384px rail width, the title still has ~150px which fits 8-9 Korean characters comfortably.

For the Goals cluster backdrop:

- **Keep `/80` and add `backdrop-blur`** — costlier (compositor work on every hover) and still leaves a faint tint, not a clean replacement.
- **Solid `bg-surface-alt` (chosen)** — zero cost, zero bleed-through, and because the row's hover state paints the same token, the cluster doesn't visually announce itself as a different surface; it reads as "the row's right side has buttons now." `shadow-sm` was added so users can tell the cluster is interactive (a subtle lift cue) without having to hover the buttons individually.

Decisive observation: `appearance-none` and a small 1rem (16px) widening together solved the text truncation without any layout-shift acrobatics, and a single token swap solved the Goals readability. Both are minimal-surface changes that don't touch interaction semantics.

Assumption that should trigger a revisit: agent name length stays ≤ 14 characters in normal use. If product introduces longer names (e.g., team-prefixed identifiers like `team-alpha-agent01-claude`), the slot will need to widen further, OR we'll need a mid-text ellipsis (`agent01…claude`) instead of trailing truncation.

## Result

- Tasks row hover: full `agent01-claude` (or any agent name up to ~14 chars) renders without ellipsis. Delete button stays at the row's right edge as before.
- Goals row hover: action cluster reads cleanly against any meta text underneath.
- All five `data-testid` selectors preserved.
- `npm run build` and `npm run test` (380/380 Vitest) pass.
- Pending: PR review + merge. Worktree dev server (port 5174) used for verification will be torn down on merge.

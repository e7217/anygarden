# fix(rooms): align right rail row right-edge with section headers (#325)

- Commit: `cc620f7` (cc620f7…)
- Author: Changyong Um
- Date: 2026-04-29
- PR: #325 (issue) — pending

## Situation

#323 (PR #324) widened the right context rail to 384px and reworked the assignee slot in `TasksSection`, but visual verification of the merged change exposed a residual alignment bug across all three rail sections. Section headers (`Responsibilities`, `Tasks`, `Shared Files`) ended their right cluster (counter + action button) 12px from the rail's right edge, but every row's *visible* content at rest stopped 24-80px short of that — the chip looked like it was floating mid-row while headers reached all the way to the right column. Playwright DOM measurement at 1440px viewport showed: header rightEdge 1428, but task slot rightEdge 1404 and goal action cluster rightEdge ~1352.

## Task

- Make every row's resting visible right edge match the headers' right edge (12px from rail) so the rail's right column reads as a single straight line.
- Don't change the hover-time interaction model (action buttons still appear on `:hover` / `:focus-within`).
- Don't shrink the title/meta truncate budget — if anything, expand it.
- Preserve the five existing `data-testid` selectors used by future E2E hooks.
- Keep this purely a frontend / CSS-positioning fix; no data plane changes.

## Action

- `packages/cluster/frontend/src/components/right-rail/TasksSection.tsx:211` — added `relative` to the row container so children can absolute-anchor.
- `packages/cluster/frontend/src/components/right-rail/TasksSection.tsx:254` — chip span gained `text-right` so its visible end now coincides with the assignee slot's right edge instead of left-floating inside a 7rem box.
- `packages/cluster/frontend/src/components/right-rail/TasksSection.tsx:263` — `<select>` gained `pr-5` to reserve a clean 20px column at its right edge for the absolute delete button to land in.
- `packages/cluster/frontend/src/components/right-rail/TasksSection.tsx:281-285` — delete button moved from a `shrink-0` flex child with `opacity-0` (which still reserved ~24px of layout space) to `absolute right-2 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100`. Its `right-2` anchor lines up with the row's `px-2` inner edge, which itself lines up with the header's `px-3` content edge.
- `packages/cluster/frontend/src/components/right-rail/GoalsSection.tsx:134` — row → `relative`.
- `packages/cluster/frontend/src/components/right-rail/GoalsSection.tsx:181` — action cluster (Run / Pause-or-Resume / Delete, three `h-6 w-6` buttons) lifted out of flex flow into `absolute right-2 top-1/2 -translate-y-1/2`, with `bg-[var(--color-surface-alt)]/80` so the buttons stay readable when they overlay the longest meta line.
- `packages/cluster/frontend/src/components/right-rail/FilesSection.tsx:74,90-95` — same pattern: row → `relative`, delete → `absolute right-2`. Second meta line gained `truncate` so long mime strings don't break out of the row when the title's truncate is already in effect.
- Verified in-browser at 1440×900 with the dev server pointed at the worktree (port 5174). Post-fix Playwright measurement returns: every header rightEdge = 1428, task slot rightEdge = 1428, hover delete rightEdge = 1428 — a single 12px-from-edge column.

## Decisions

Sources mined: PR #324 description, the brainstorming exchange that produced #325 (the diagnosis with measured offsets), and the diff itself. The pre-fix code already showed why each alternative wasn't picked, but the rationale was not previously written down.

Three alternatives were weighed before landing on absolute-positioned action buttons:

- **Always show the action buttons (low opacity at rest)** — would have aligned the right edge cleanly because the buttons would always be the rightmost element. Rejected because it violates the rail's "calm at rest" design (DESIGN.md §4 — interactive affordances appear on hover, not at rest), and Goals rows would carry three persistent buttons that visually compete with the title.
- **Drop the buttons from layout via `display: none` → `display: flex` on hover** — would also fix the alignment because the chip/meta would reflow to the row's right edge. Rejected because the layout shift on every hover is jarring (the title's `flex-1` width changes, causing a reflow and possibly retriggering truncation), especially noticeable in Goals where three buttons appear/disappear at once.
- **Absolute-position the action buttons inside a `relative` row, with right-side padding on the live element underneath (`pr-5` on the task select, semi-transparent background on the goal action cluster)** — chosen. The chip / meta column is now the last flex child and naturally extends to the row's inner right edge with no layout shift. The `pr-5` / `bg-surface-alt/80` shims handle the only downside (visual collision between the absolute button and the live element underneath on hover).

Decisive observation: the chip + select swap from #323 already used the same `relative + absolute inset-0` pattern, so extending that idiom to the trailing action button keeps the row's mental model consistent. One pattern, two uses.

Assumption that should trigger a revisit: the Goals action cluster's `bg-[var(--color-surface-alt)]/80` works because the row's hover background is the same surface-alt token. If the rail's row hover treatment changes, the cluster's backdrop will need to change with it.

## Result

- All three rail sections now have the same visible-right-edge alignment at rest: 12px from the rail's right border, matching the header's counter/action cluster.
- Hover-time interaction is unchanged: pointing at a row brings up the same buttons, but they overlay the right side of the row instead of pushing the chip/meta around.
- Title/meta truncate budget gained 24-76px depending on the section because the action buttons no longer reserve flex space at rest.
- All five existing `data-testid` selectors are present at the same DOM locations.
- `npm run build` (tsc + vite) and `npm run test` (Vitest 380/380) pass on the fix branch.
- Pending: PR review + merge. The worktree dev server (port 5174) used for verification will be torn down on merge.

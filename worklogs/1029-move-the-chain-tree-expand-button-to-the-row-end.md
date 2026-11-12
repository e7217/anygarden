# fix(chat): move the chain tree expand button to the row end (#1029)

- Commit: `2a3efb3` (2a3efb3acf0a9125ce94dcc5e3690fe38a631a57)
- Author: Changyong Um
- Date: 2026-11-12T15:24:29+09:00
- PR: #1029

## Situation

`PeerChainTree` placed the expand/collapse button at the leading edge of
each row. Because the button column was reserved for every row whenever
any row had children, every row — including the root — carried a 32 px
(44 px touch) empty column before its label text. The result was a
visually noisy leading gap across the whole tree even on rows that could
never be toggled. Children were indented with inline `paddingInlineStart`
per depth level but had no visible guide connecting them to their parent.

## Task

- Remove the leading expand button and its per-row `paddingInlineStart`
  indent.
- Move the expand/collapse control to the **trailing** (end) position of
  each expandable row.
- Replace the per-row `paddingInlineStart` indent with a vertical guide
  line rendered as a CSS border on the left of each open branch container.
- Keep the `ToggleSlot` placeholder so row highlight widths stay uniform
  when the tree has toggles.
- Update tests: rename the existing toggle-column test to match the new
  trailing semantics, verify button position, add a branch-guide-line test.
- Swap `ChevronRight` (rotated on expand) for `ChevronDown` (rotated 180°
  on collapse) to match the trailing chevron convention.

## Action

**`PeerChainTree.tsx`**

- `import { ChevronDown, … }` replaces `ChevronRight`.
- Extracted `renderBranch(entries, level, parentName)` renders a `<ul
  data-testid="peer-chain-branch">` with `ms-2 border-s border-[var(--color-border)]
  ps-2` as long as `level <= MAX_TREE_INDENT`, providing the visual guide
  line. Previously children were rendered inline as a plain `<ul>` inside
  `renderRow`.
- In `renderRow` the `<div>` wrapper's `paddingInlineStart` style was
  removed; the expand `<Button>` and `<ToggleSlot>` were moved after the
  row `<button>` label (lines 105–116, previously 76–97).
- `ChevronDown` rotates `180°` when expanded instead of `ChevronRight`
  rotating `90°`.
- Root row: `ToggleSlot` moved from before the root `<button>` to after
  it (lines 180–196); root children rendered via `renderBranch(tree.roots,
  1, …)` instead of a plain `<ul>`.

**`PeerChainTree.test.tsx`**

- Existing test renamed: "lines every row up on one toggle column, or
  none when nothing folds" → "keeps the expand control at the end of each
  row, in one column or none when nothing folds".
- Added assertion: each `[data-toggle-slot]` has a preceding sibling
  `BUTTON` (the row selector button), confirming trailing placement
  (line 74).
- Added assertion: the toggle button itself has no `nextElementSibling`,
  confirming it is last in the row (lines 75–76).
- New test "groups each open branch under one guide line": asserts
  `getAllByTestId('peer-chain-branch')` returns 4 elements for the default
  sample tree (root's question nodes + the open D→E→F path).

## Decisions

- **Trailing button vs. leading button**: The leading button consumed
  horizontal space on every row of a tree that might contain toggleable
  rows, even non-toggleable ones. Moving the button to the trailing edge
  lets the label start immediately at its natural indent, eliminating the
  gap without changing the reserve-column-when-needed behaviour of
  `ToggleSlot`.
- **`renderBranch` with CSS border guide vs. SVG/Canvas connectors**:
  A simple `border-s` on the `<ul>` wrapper achieves the guide line in one
  CSS property, tokens the border colour via `--color-border`, and needs
  no extra markup per row. SVG connectors were considered but rejected as
  disproportionate complexity for a standard indent pattern. The existing
  `ms-2 ps-2` spacing already matched the design system's spacing tokens.
- **`ChevronDown` + 180° rotation vs. separate up/down icons**: Using one
  icon and CSS rotation keeps the animation smooth (consistent `transition-
  transform`) and avoids an icon bundle split. This aligns with how other
  collapsible controls in the project use a single chevron.
- **Assumption**: `--color-border` and `--control-icon-size` CSS tokens
  are defined globally; the guide line depends on them. If the token is
  removed or renamed this border will silently become invisible.

## Result

- The leading 32 px gap before every label is gone; labels start at their
  natural indent position.
- The down/up chevron now appears at the right end of each expandable row.
- Each open branch hangs visually below its parent's label via a vertical
  guide line (`border-s`), making the hierarchy legible without per-row
  padding arithmetic.
- All existing and new tests pass.
- No pending items.

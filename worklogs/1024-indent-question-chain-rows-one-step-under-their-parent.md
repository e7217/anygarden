# fix(chat): indent question chain rows one step under their parent (#1024)

- Commit: `e64d34f` (e64d34f1c1e1d3451e0cfad1ed22e5da102f0f18)
- Author: Changyong Um
- Date: 2026-11-12T14:47:50+09:00
- PR: #1024

## Situation

`PeerChainTree` renders a hierarchical list of agent rows, each indented one level per depth. The root row was rendered as a plain `<button>` with no leading expand-button column, while every question row always reserved space for that column (either a real `<Button>` or a `<span>` placeholder). This structural mismatch pushed level-1 labels ~52 px (64 px on touch) to the right of the root label instead of the intended single 16 px indent step, breaking the visual alignment of the tree.

## Task

- Fix the indentation so each child row sits exactly one 16 px step right of its parent.
- Ensure the expand-button column is present for every row (including root) when any row can fold, and absent for all rows when nothing folds.
- Shorten visible row labels so only the answerer is shown when the asker is the row immediately above; keep the full `A → B` path in the accessible name and in filter notices.
- Add tests covering the new label behaviour and the toggle-slot alignment rule.

## Action

**`PeerChainTree.tsx`**

- Added `shortRowLabel(entry, parentName)` (`PeerChainTree.tsx:23–26`): returns `entry.node.target_name` when `entry.node.caller_name === parentName`, otherwise falls back to the full `rowLabel`.
- Extracted `ToggleSlot` component (`PeerChainTree.tsx:29–32`): a fixed-size `<span aria-hidden>` with `data-toggle-slot` that occupies the expand-button column without rendering a button.
- Computed `hasToggles` (`PeerChainTree.tsx:59–61`): `true` when at least one row in the tree has children; drives whether any row reserves the column at all.
- Wrapped the root `<button>` in `<div className="flex … items-center gap-1">` and prepended `{hasToggles && <ToggleSlot />}` so the root aligns with its children (`PeerChainTree.tsx:178–196`).
- Changed leaf-row fallback from an unconditional `<span>` placeholder to `hasToggles && <ToggleSlot />` (`PeerChainTree.tsx:95–97`), so flat trees emit no extra columns.
- Added `data-toggle-slot` to the real expand `<Button>` (`PeerChainTree.tsx:88`) to let tests count occupied column slots.
- Updated `renderRow` signature to accept `parentName: string` and pass `node.target_name` down to children (`PeerChainTree.tsx:71, 121`).
- Replaced visible label `{label}` with `{shortRowLabel(entry, parentName)}` and added `data-testid="peer-chain-row-label"` (`PeerChainTree.tsx:109–111`).
- Root call site now passes `chain.root.name` as the initial `parentName` (`PeerChainTree.tsx:198`).

**`PeerChainTree.test.tsx`**

- Updated the existing "names the row" assertion from `F → G` to `G` to match the new short-label rule (`PeerChainTree.test.tsx:32`).
- Added `node` to the import from `peerChains.fixtures` (`PeerChainTree.test.tsx:9`).
- New test: "names only the answerer … keeping the full path for assistive tech" — checks that `[data-node-id="E"]` label text is `E` and that the filter button accessible name still carries the full `D → E` path (`PeerChainTree.test.tsx:59–64`).
- New test: "keeps the asker on a row whose parent question is missing" — renders a node with `parent_id: 'missing'` and asserts the label shows `X → Y` (`PeerChainTree.test.tsx:66–70`).
- New test: "lines every row up on one toggle column, or none when nothing folds" — counts `[data-toggle-slot]` nodes; expects 6 (root + 5 shown rows) for the full sample, 0 for a two-node flat tree (`PeerChainTree.test.tsx:72–80`).

## Decisions

- **Reserve column only when needed**: an earlier approach always rendered the placeholder `<span>`, which kept alignment correct for trees that have both expandable and leaf rows. However, it meant flat single-level trees gained an unnecessary 52/64 px leading gap. The `hasToggles` gate removes the column entirely for flat trees without touching any per-row logic.
- **Short label vs. full label**: showing only the answerer reduces visual noise in deep chains where every row is a child of the one above; the full `A → B` label is redundant when `A` is the row you just read. The fallback to the full label for orphaned rows (missing parent in a partial payload) prevents confusion where the asker would otherwise be invisible.
- **`ToggleSlot` component**: the `<span>` was inlined before. Extracting it to a named component makes the intent clear and lets tests locate slots via `data-toggle-slot` without fragile CSS-selector chains.
- Assumption: `chain.root.name` is never equal to any node's `caller_name` in a way that would suppress an arrow that should be shown. If a root agent can also appear as a caller inside the tree, the short-label rule would silently elide arrows for its direct children — this would need revisiting.

## Result

- Level-1 rows now sit one 16 px indent step right of the root row, matching the design intent.
- Flat (non-expandable) trees render no leading column gap.
- Visible labels show only the answerer when the asker is the row above; accessible names and filter notices retain the full path.
- Three new tests cover the label shortening rule, the orphan-row fallback, and the toggle-slot alignment invariant. All existing tests pass with the updated `F → G` → `G` expectation.

# refactor(agents): restore whisper divider between Settings dialog sections (#170)

- Commit: `4f7d0d8`
- Author: Changyong Um
- Date: 2026-04-19
- PR: #170

## Situation

#167 removed the `divide-y` seam between Settings dialog sections
citing DESIGN.md §6.1 "No hard section borders — separation comes
from background color changes and spacing". Visual review surfaced
that the pure-gap (`space-y-6`) layout made the four sections read
as a single blurred block: the 24px gap is enough negative space on
a full web page where warm-white section backgrounds alternate, but
inside a bounded dialog the ambient rhythm isn't there and the eye
loses the section boundaries.

## Task

- Reintroduce a subtle 1px seam between sections without
  contradicting DESIGN.md.
- Keep the spacing balanced so the divider doesn't feel cramped.
- Preserve every behavior added in the previous two iterations:
  collapsible Activity, compact Rooms rows, stacked layout.

## Action

`packages/cluster/frontend/src/components/AgentSettingsDialog.tsx`:
the scroll-body wrapper moved from
`px-6 py-5 space-y-6`
to
`px-6 py-5 divide-y divide-[var(--color-border)] [&>section]:py-4 [&>details]:py-4 first:[&>*]:pt-0 last:[&>*]:pb-0`.

The child selectors apply `py-4` to both `<section>` (plain) and
`<details>` (the CollapsibleSection wrapper for Activity) so both
section types share the same rhythm. `first:[&>*]:pt-0` /
`last:[&>*]:pb-0` keep the seam from touching the top or bottom
edges of the scroll area.

Total vertical gap between two section contents is now 32px
(16px padding above seam + 1px seam + 16px padding below), matching
the `space-y-8` (32px) that the design-review chat called for.

## Decisions

Revised the §6.1 interpretation from #167. Re-reading DESIGN.md
surfaced that §2 explicitly lists the whisper border as the
primitive for "cards, dividers, **sections**" — §6.1 forbids a
*hard* section border (heavy weight, saturated color), not a 1px
whisper.

Considered and rejected:

- **Card-per-section** (`bg-white border rounded shadow`): the
  Manifest panel already contains its own 2-column tree+editor
  grid, so wrapping Manifest in another card produces a
  card-in-card nesting that fights the dialog's visual hierarchy.
  Works for Overview/Rooms/Activity but not Manifest — so the
  treatment has to be applied symmetrically, which rules it out.
- **Warm-white alternation** (Overview/Rooms white, Manifest/
  Activity `#f6f5f4`): matches §5.3 literally, but strips to two
  rhythms that alternate every section, which reads as noisy inside
  a single modal rather than "gentle visual rhythm" (§5.3 assumes
  page-sized sections separated by generous padding).

The divider + 32px gap is the minimum change that gives the eye a
seam without disturbing the stacked scan experience.

Assumption that would trigger revisiting: if Settings later grows
from 4 sections to 6+ and the divider stack starts feeling like a
spreadsheet, switch to card-per-section with Manifest as an
exception. No signal of that today.

## Result

- `npm test` — 245 passed, 25 files (test behavior unchanged; the
  existing "sections in document order" check still passes because
  `divide-y` doesn't alter DOM structure).
- `npm run build` — tsc + vite clean.
- Visual effect: each section content block is anchored by a
  whisper-thin horizontal line above/below (except the first/last,
  which sit against the dialog edges). 32px of breathing room
  between section contents, consistent with the 8px-base scale
  DESIGN.md §5.1 recommends.

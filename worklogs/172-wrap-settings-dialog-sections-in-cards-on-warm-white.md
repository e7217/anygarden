# refactor(agents): wrap Settings dialog sections in cards on warm-white body (#172)

- Commit: `6447ef6`
- Author: Changyong Um
- Date: 2026-04-19
- PR: #172

## Situation

#170 put a whisper divider back between sections, but at 1px
`rgba(0,0,0,0.1)` it was too subtle to actually register — the user
pulled latest and reported "the sections look the same as the
undivided version from #167". The whisper weight is right for the
design system, but a single pixel isn't enough *alone* to section
a bounded dialog with already-dense content blocks.

## Task

- Give each section an unambiguous visual boundary without breaking
  the DESIGN.md primitives.
- Do it with the same Notion card treatment the rest of the app
  already uses (`shadow-card`, `--radius-lg`, whisper border on
  `bg-white`) — not a custom variant.
- Keep Manifest's internal 2-column tree + editor rendering flat
  inside its card (no card-in-card nesting).
- Preserve every behavior from the earlier iterations: stacked
  layout, collapsible Activity, compact Rooms rows.

## Action

`packages/cluster/frontend/src/components/AgentSettingsDialog.tsx`:

- Added a shared `SECTION_CARD_CLASS`
  (`bg-white rounded-[var(--radius-lg)] border border-[var(--color-border)] shadow-card p-5`).
  `--radius-lg` is 12px, `shadow-card` is the 4-layer stack from
  `src/index.css:84-88` — both DESIGN.md §4 defaults.
- Applied that class to both `<Section>` and `<CollapsibleSection>`
  so the four sections wear identical card chrome. Heading + body
  spacing (`space-y-3`) stays inside the card.
- Switched the scroll-body wrapper from
  `px-6 py-5 divide-y … [&>section]:py-4 [&>details]:py-4 first:[&>*]:pt-0 last:[&>*]:pb-0`
  to
  `px-6 py-5 space-y-3`
  on a container that carries `bg-[var(--color-surface-alt)]`
  (`#f6f5f4` warm white). 12px gap between cards is enough because
  each card's shadow + border already does the heavy lifting.
- Activity's `<details>` inherits the same card chrome, so the
  collapsed state shows a single-line card that expands to reveal
  the log — matches the other three visually.

## Decisions

This is the third spacing iteration in as many PRs. Weighed
explicitly:

- **Thicker divider (2-3px or saturated color)** — rejected,
  contradicts DESIGN.md §2's insistence on whisper-weight borders
  throughout.
- **Single card wrapping all four sections** — rejected, loses the
  per-section separation the user actually asked for; just an
  outer frame doesn't help section-level scanning.
- **Card-per-section (picked)** — matches DESIGN.md §4 exactly.
  The earlier hesitation (in #167's brainstorm chat) was Manifest
  producing card-in-card nesting, but a closer reading of
  ManifestPanel showed its 2-column grid has no card chrome of its
  own, so it sits inside the outer card flat. No nesting issue.
- **Warm-white body** — applying DESIGN.md §5.3 "Warm alternation"
  at modal scope. The one-step color shift between the `#f6f5f4`
  body and `#ffffff` cards does most of the section-separation
  work; the shadow + border add the final touch. Rejected keeping
  the body white because on white-on-white the soft shadow alone
  was still subtle enough to not clearly define cards.

Assumption that would trigger revisiting: if a future panel needs
its own card-like chrome (e.g. a new section that itself uses
`<Card>` components), the outer card + inner card nesting would
become visually noisy. No such case today.

## Result

- `npm test` — 245 passed, 25 files (no new tests; card classes
  are visual-only and don't affect DOM structure that the suite
  asserts on).
- `npm run build` — tsc + vite clean.
- Visual effect: dialog body is warm-white, each of the four
  sections floats as a distinct white card with a whisper border
  and soft shadow. Activity section shows as a single-line card
  that expands to reveal the log. No room for the "sections
  blending" impression.

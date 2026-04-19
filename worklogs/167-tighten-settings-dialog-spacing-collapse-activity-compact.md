# refactor(agents): tighten Settings dialog spacing, collapse Activity, compact Rooms (#167)

- Commit: `fc0c2cc` (fc0c2cc83ee3def661d3072dde9d2aebf4705490)
- Author: Changyong Um
- Date: 2026-04-19T14:30:30+09:00
- PR: #167

## Situation

After #165 moved Agent Settings to a single-page stacked layout, a
DESIGN.md re-read plus a first impression surfaced three polish
gaps: the `divide-y` + `py-5` inter-section seam created ~40px of
awkward empty space between sections (and contradicted DESIGN.md
§6.1 "No hard section borders — separation comes from background
color changes and spacing"); Activity, the lowest-frequency section,
was expanded by default and pushed Manifest/Rooms further down the
scroll; and each Rooms row carried `py-2` + full-size icon buttons,
making a list of 10+ rooms taller than it needed to be.

## Task

- Replace the per-section `py-5` + whisper divider with a pure-gap
  rhythm that reflects DESIGN.md's "spacing over borders" rule.
- Hide the Activity body by default without removing it — it still
  needs to be reachable with one click.
- Trim vertical space in each Rooms row without breaking tap
  targets for the add/remove actions.
- Keep every panel's internal content unchanged; these are all
  shell-level tweaks.

## Action

- `packages/cluster/frontend/src/components/AgentSettingsDialog.tsx`:
  - Dropped `divide-y divide-[var(--color-border)]` and the
    `[&>section]:py-5 first:[&>section]:pt-0 last:[&>section]:pb-0`
    selector on the scroll body, replacing both with plain
    `space-y-6` (24px vertical gap).
  - Extracted the shared heading classes into
    `SECTION_HEADING_CLASS` and added a `CollapsibleSection`
    variant built on native `<details>` + `<summary>` with a
    rotating `ChevronRight` (group-open:rotate-90) that matches the
    existing section heading typography.
  - Wrapped Activity in `<CollapsibleSection>` with
    `defaultOpen={false}`.
- `packages/cluster/frontend/src/components/agent-settings/RoomsPanel.tsx`:
  - Changed the assigned/available row padding from `px-3 py-2` to
    `px-3 py-1.5`.
  - Overrode the icon button's default `h-9 w-9` to `h-7 w-7` via
    className so the button no longer sets a hard floor on row
    height.
- `packages/cluster/frontend/src/components/AgentSettingsDialog.test.tsx`:
  - Reworked the order test — `<details>` has no implicit `region`
    role, so `getAllByRole('region')` now only sees three sections.
    Switched to `compareDocumentPosition` between the four section
    test IDs.
  - Added a new case that asserts Activity renders as
    `<details>` with `.open === false` by default.

## Decisions

Sources: issue #167 body, DESIGN.md §4/§6.1/§5 (spacing and
elevation), this conversation's design-review exchange.

- **Keep a whisper border between sections vs. pure-gap spacing.**
  Considered: keep `divide-y` but drop the inner `py-5` to tighten
  the seam. Rejected because the explicit DESIGN.md §6.1 rule calls
  out "no hard section borders" and the divider read as an
  unnecessary visual line inside an already-bounded dialog. The
  24px gap is enough signal on its own now that sections have
  internal `space-y-3` headings.
- **Collapsible Activity via `<details>` vs. JS-controlled state
  with a Radix `Collapsible` wrapper.** Picked `<details>`: it is
  keyboard-accessible for free, the `group-open:rotate-90` Tailwind
  variant handles the chevron state without React state, and the
  summary/body DOM is well-understood by screen readers. Rejected
  the Radix route as YAGNI — there is no custom keyboard behavior
  this section needs that `<details>` doesn't already provide.
- **Smaller icon button via `size="icon"` override vs. `size="sm"`.**
  `size="sm"` changes padding and adds text-gap affordances; the
  rooms row already centers a single icon, so overriding `h-7 w-7`
  on the existing `size="icon"` is the minimal change that lowers
  the row ceiling. Rejected switching sizes entirely because that
  would ripple into other usages if the default changes later.

Assumption that would trigger revisiting: if Activity becomes a
hot path (e.g. new lifecycle states admins want to monitor), the
default-closed stance flips to default-open and `<details>` gets
an `open` prop or becomes a plain `<Section>` again. No signal of
that today.

## Result

- `npm test` — 244 → 245 (new Activity-collapsed-by-default case),
  25 files, all green.
- `npm run build` — tsc + vite clean.
- Visual effect: four sections separated by even 24px gaps, no
  divider lines; Activity shows only its heading with a right-facing
  chevron until clicked; Rooms rows are ~10px shorter each, so a
  10-room list now fits in roughly the space an 8-room list did
  before.
- Still pending: hands-on visual check against DESIGN.md (warm
  palette, focus ring on the `<summary>` row) before calling the
  dialog visually "done". No new risks introduced.

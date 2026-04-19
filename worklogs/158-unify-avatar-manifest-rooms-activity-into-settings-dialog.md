# feat(agents): unify avatar/manifest/rooms/activity into Settings dialog (#158)

- Commit: `1239440` (1239440cd501d08aa151912db6e3c23f74d22070)
- Author: Changyong Um
- Date: 2026-04-19T13:46:58+09:00
- PR: #158

## Situation

The `⋯` menu on an agent row fanned out to four independent dialogs
(AvatarPickerDialog / AgentEditDialog / AgentRoomsDialog /
AgentHistoryDialog) plus three inline menu items (Copy agent ID, the
context-window opt-out toggle, Delete agent). Admins had no single
"what is this agent?" surface — checking the avatar, manifest, and
recent activity meant opening three dialogs in sequence. Worse,
**Copy agent ID was broken in practice**: the handler called
`navigator.clipboard.writeText` but silently swallowed failures (in
insecure contexts / denied permissions) and gave no success feedback,
so admins had no way to confirm anything copied.

## Task

- Collapse the four stand-alone dialogs into one `AgentSettingsDialog`
  with a left-rail nav (Overview / Manifest / Rooms / Activity).
- Shrink the `⋯` menu to three items: **Settings…** (dialog
  entry-point), the opt-out toggle, and **Delete agent**.
- Fix Copy-ID by surfacing the agent ID as visible monospace text +
  Copy button with explicit "Copied" and "Clipboard unavailable"
  feedback.
- Keep `topology/DetailPanel.tsx`'s independent "Manage rooms" entry
  point working — it opens a focused rooms-only flow from a node
  detail panel and would be overwhelmed by the full Settings dialog.
- Preserve manifest editor behavior wholesale: file tree + virtual
  AGENTS.md row, skill-aware recursive tree, attached-skills section,
  bulk Save semantics — nothing in the editing flow should regress.
- Hold the 231-test vitest baseline and add coverage for the new
  surfaces (Overview, AgentSettingsDialog, renamed panels).

## Action

- **New outer shell**: `packages/cluster/frontend/src/components/AgentSettingsDialog.tsx`
  renders a `max-w-5xl` Dialog with a 200px left nav + right pane
  grid; panels mount conditionally per selection. Header carries the
  agent name, PresenceDot, and engine tag.
- **New identity section**:
  `packages/cluster/frontend/src/components/agent-settings/OverviewPanel.tsx`
  combines avatar (click-to-expand `AvatarPickerPanel`), name (inline
  `<Input>` with blur/Enter commit + Escape rollback), ID (monospace
  badge + Copy button with `Check`/`Copy` icon swap), engine, and
  state. Copy falls back to `window.getSelection().selectNodeContents`
  on clipboard rejection and shows "Clipboard unavailable — text
  selected".
- **Extracted panels** (Dialog wrappers stripped, public surface kept
  compatible):
  - `agent-settings/ManifestPanel.tsx` — from `AgentEditDialog.tsx`.
    Removed `open`/`onOpenChange` props, collapsed the Close/Save
    footer down to just Save (the outer Dialog's X/Esc own dismiss
    now), and re-wired the "View in Skills" link through a new
    `onNavigateAway` callback so the parent closes the dialog before
    `navigate('/admin/skills')`.
  - `agent-settings/AvatarPickerPanel.tsx` — from
    `AvatarPickerDialog.tsx`. Save/Cancel both fire `onDone`, letting
    the parent collapse the inline picker.
  - `agent-settings/RoomsPanel.tsx` — from `AgentRoomsDialog.tsx`.
    `open`-gated fetch dropped; mount = fetch.
  - `agent-settings/ActivityPanel.tsx` — from `AgentHistoryDialog.tsx`.
- **Thin wrapper for topology**:
  `packages/cluster/frontend/src/components/AgentRoomsDialog.tsx` is
  now a Dialog shell around `RoomsPanel`, so
  `topology/DetailPanel.tsx` stays unchanged and shares the same
  rooms UI as the settings dialog.
- **Menu rewrite**:
  `packages/cluster/frontend/src/components/AgentSettingsMenu.tsx`
  drops `onEditAvatar` / `onEditManifest` / `onManageRooms` /
  `onShowActivity` / `onCopyId` props in favor of a single
  `onOpenSettings`. Delete stays below a divider; opt-out toggle
  keeps the trailing check mark.
- **Caller migrations**: `Sidebar.tsx` and `AdminMachines.tsx` each
  collapsed four `useState` pairs (+ four handlers) into one
  `settingsOpen`/`settingsAgent` pair and one `handleOpenSettings`.
  Both mount a single `AgentSettingsDialog` instead of four dialogs.
- **Test migration**: `AgentEditDialog.test.tsx` (28) →
  `agent-settings/ManifestPanel.test.tsx` (render harness swap),
  `AvatarPickerDialog.test.tsx` (4) →
  `agent-settings/AvatarPickerPanel.test.tsx` (5 — added a Cancel
  test). New: `agent-settings/OverviewPanel.test.tsx` (9 —
  name/avatar/copy happy paths + clipboard-rejection fallback) and
  `AgentSettingsDialog.test.tsx` (3 — nav rendering, section
  switching, closed-state no-panel). `AgentSettingsMenu.test.tsx`
  rewritten for the new three-item surface. Final suite: 244 tests
  across 25 files.
- **Deletions**: `AgentEditDialog.{tsx,test.tsx}`,
  `AvatarPickerDialog.{tsx,test.tsx}`, `AgentHistoryDialog.tsx`.

## Decisions

Five shaping decisions from brainstorming, recorded in
`.tmp/plan-158-agent-settings-unified-dialog.md` §3.2:

- **Scope of unification — Avatar+Manifest+Rooms+Activity+ID, Delete/
  opt-out stay on menu.** Considered: (a) only the three dialogs,
  keeping Rooms and ID on menu; (c) absorb everything including
  Delete + opt-out. Rejected (a) because Copy-ID was already broken
  and Rooms is another agent-attribute surface that belongs next to
  identity; rejected (c) because Delete being a menu-bottom red
  item preserves destructive-action muscle memory and the opt-out
  toggle's single-click value is lost if moved into a tab.
- **Layout — left sidebar nav over top tabs or a single scroll.**
  Considered: (b) horizontal tabs — rejected because Manifest's
  existing 2-column tree+editor grid collides visually with a tab
  bar; (c) single vertical scroll — rejected because the Manifest
  editor (1458 lines of functionality) gets trapped inside an outer
  scroll container and becomes unusable. The sidebar nav's 2-level
  hierarchy composes cleanly with Manifest's internal grid.
- **Menu collapse — one entry, not per-section deep-links.**
  Rejected "each menu item opens the dialog to its matching tab"
  because all five items would then open the *same* dialog with only
  the initial tab different, defeating the consolidation intent. A
  single "Settings…" preserves a clean mental model of one entry +
  one destructive + one quick toggle.
- **Avatar lives inside Overview, not as a fifth section.** Avatar
  and name are identity pair; separating them into a standalone
  section weakens the "this is the agent's home" reading of Overview
  and inflates the nav to 5 items. The picker's grid layout fits
  inline without modal-in-modal.
- **AgentRoomsDialog kept as a thin wrapper.** Considered removing it
  and routing topology through the full Settings dialog — rejected
  because the topology node panel exposes a focused "just rooms"
  intent; opening a 1024px Settings dialog for that is overkill. The
  wrapper and the Settings dialog both mount the same `RoomsPanel`
  so they can't drift.

Assumptions that would trigger revisiting: (1) if admins end up
bouncing between Manifest and Activity mid-edit, the conditional-
render panel strategy will need to flip to `display:none` hiding so
Manifest's unsaved edits survive section switches (risk flagged in
plan §6.2); (2) if `created_at` lands on the `Agent` type, the
Overview Created row becomes a trivial add (risk §6.3).

## Result

- 231 → 244 vitest tests passing; backend 587 passing (no regression
  from a frontend-only change).
- Copy-ID path now gives users unambiguous feedback instead of
  silently failing in insecure contexts.
- `⋯` menu surfaces 3 items instead of 7; every non-destructive
  agent-property path funnels through one dialog.
- `topology/DetailPanel.tsx` continues to work unchanged.
- Still pending: dev-server visual verification against DESIGN.md
  (warm-neutral palette, whisper borders, single-accent brand color)
  for the new OverviewPanel UI and the AgentSettingsDialog nav pill;
  planned before PR review.

# fix(ui): pass fetchEngineCatalog to AgentSettingsDialog from Sidebar (#245)

- Commit: `d4b3490` (d4b349066d92bc81c0833ab96b4a3de14475f5ad)
- Author: Changyong Um
- Date: 2026-04-23T00:34:49+09:00
- PR: #245

## Situation

`AgentSettingsDialog` is a single shared component but has two entry
points: the sidebar AGENTS row's "Settings…" menu and the machines page
per-agent row. After #217 wired Model / Reasoning effort dropdowns into
the Overview panel, only the machines page entry point showed them —
opening the same dialog from the sidebar rendered Overview without any
model controls. Users reasonably expected one dialog to behave one way.

## Task

- Diagnose why identical component invocations produced different UIs
- Align the sidebar entry point with the machines page so both show
  the Model / Reasoning effort dropdowns
- Keep the fix minimal; avoid touching the shared dialog or the
  intentional "catalog unavailable → hide dropdowns" path in
  `OverviewPanel` (kept for echo and other catalog-less engines)

## Action

- `packages/cluster/frontend/src/components/Sidebar.tsx:940` — added
  `fetchEngineCatalog` to the `useAgents()` destructure in
  `AgentDMListAdmin`. The hook already exported it
  (`useAgents.ts:325,358`); the sidebar was the only caller that hadn't
  wired it up.
- `packages/cluster/frontend/src/components/Sidebar.tsx:1290` —
  forwarded `fetchEngineCatalog={fetchEngineCatalog}` to
  `<AgentSettingsDialog>`, matching the call shape at
  `AdminMachines.tsx:887`.

## Decisions

Three options surfaced in `.tmp/plan-245-sidebar-agent-settings-engine-catalog.md`:

- **A. Forward the prop from Sidebar (chosen)** — one-file, two-line
  parity fix that mirrors the already-correct `AdminMachines` caller.
- **B. Move `useAgents()` inside `AgentSettingsDialog`** — would
  eliminate the foot-gun by removing caller responsibility entirely,
  but the dialog's current contract is "caller owns data wiring." That
  contract keeps mocking trivial in tests and leaves room for future
  entry points to opt out of specific data (e.g. admin-only catalogs).
  Rejected: solves a bigger design problem than we have.
- **C. Drop the "no fetchEngineCatalog → hide dropdowns" branch in
  `OverviewPanel`** — would make the bug impossible but regresses
  echo / catalog-less engines into showing empty dropdowns. The branch
  at `OverviewPanel.tsx:115-118` is intentional per the prop-level
  comment in `AgentSettingsDialog.tsx:65-69`. Rejected: trades a
  caller-side omission for a user-visible regression.

Decisive observation: the real defect is asymmetry between two callers
of a correctly-designed shared component, not a design flaw in the
component itself. Fixing the caller preserves the prop-optional
contract that echo relies on.

Assumption worth revisiting if violated: `AgentDMListAdmin` stays
admin-gated so `useAgents()` keeps returning a usable
`fetchEngineCatalog`. If the sidebar agent list ever renders for
non-admins, this prop's behavior in that context needs review — today
the component isn't mounted at all for non-admins, so it's a no-op.

## Result

- Sidebar "Settings…" now shows the Model / Reasoning effort
  dropdowns and persists selections through the same `updateAgent`
  path the machines page uses.
- Existing unit tests (`AgentSettingsDialog.test.tsx`,
  `AgentSettingsMenu.test.tsx`) still green — 18/18.
- `npm run build` (tsc + vite) clean.
- No regression for catalog-less engines: `OverviewPanel`'s
  "catalog unavailable" branch is unchanged and still hides the
  dropdowns when `fetchEngineCatalog` returns `null`.

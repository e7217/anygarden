# feat(agents): model and reasoning_effort editing in Settings Overview (#217)

- Commit: `234923f` (234923fd850f2b163aea12b9446bb4f569307ef8)
- Author: Changyong Um
- Date: 2026-04-21T15:11:25+09:00
- PR: #217

## Situation

Issue #6 (catalog 4.6 update) had wired model + reasoning-effort pickers into the "Create Agent on Machine" dialog in `AdminMachines.tsx`, and the backend `PUT /api/v1/agents/{id}` route had been accepting `model` / `reasoning_effort` updates with the standard `*_set` idiom ever since. The edit flow never got the matching UI, though — `AgentSettingsDialog` → `OverviewPanel` only exposed name, avatar, ID, engine (read-only), and state. An admin who wanted to retarget an existing agent to a different model had to either delete-and-recreate or poke at the DB directly. PR #216 refreshed the catalog values this week, which made the gap more visible: the new models were only reachable from the create flow.

## Task

- Surface Model + Reasoning dropdowns in the per-agent Settings dialog so the value an admin picks actually round-trips through the existing `updateAgent` → `PUT` → `bump_generation` → respawn pipeline.
- Keep the OverviewPanel's current "pick = save" commit pattern consistent (name blur-commits, avatar pick-commits, so dropdowns should change-commit) — no Save button, no modal confirmation.
- Don't silently drop legacy values. Agents with a `model` that was removed from the catalog in #216 (e.g. a hypothetical `claude-opus-4-6-fast`) must still show what's actually stored.
- Make the new prop optional so tests that don't care about dropdown wiring stay terse, and so any future caller of `AgentSettingsDialog` that hasn't wired the catalog yet degrades to read-only instead of crashing.
- No backend changes — the PATCH route has been ready.

## Action

Frontend-only, six files, +334 / -12.

- **`packages/cluster/frontend/src/hooks/useAgents.ts`** — extended the `updateAgent` patch type with `model?: string | null; model_set?: boolean; reasoning_effort?: string | null; reasoning_effort_set?: boolean`. No behavioral change; just a type surface the Overview panel can call through.
- **`packages/cluster/frontend/src/components/AgentSettingsDialog.tsx`** — imported `EngineCatalog`, added an optional `fetchEngineCatalog` prop, mirrored the new `updateAgent` patch shape in the local prop type, and forwarded both to `OverviewPanel`.
- **`packages/cluster/frontend/src/components/AdminMachines.tsx`** — single-line addition: the caller now passes `fetchEngineCatalog={fetchEngineCatalog}` (already exposed by `useAgents`) into the dialog.
- **`packages/cluster/frontend/src/components/agent-settings/OverviewPanel.tsx`** — the bulk of the change:
  - New `CatalogState` union (`loading` / `ready` / `unavailable`) plus a module-level `SELECT_CSS` copied from `AdminMachines.tsx:267` so both dialogs render identical selects.
  - New `useEffect` keyed on `agent.engine` + `fetchEngineCatalog` that resolves the catalog with a `cancelled` guard against stale races.
  - New `useMemo` narrowing reasoning levels per-model (same logic as `AdminMachines.tsx:184-191`).
  - Two new commit handlers, `handleModelChange` / `handleReasoningChange`, that fire `updateAgent(id, { model: value \|\| null, model_set: true })` shapes on change, flip `configSaving` around the PUT, and surface failure via a new `overview-config-error` node.
  - Two new `<dl>` rows rendered only when `catalogState.kind === 'ready'`: a Model `<select>` (default placeholder + all catalog models + a disabled "Current: X (no longer in catalog)" option when the stored value isn't in the list) and a Reasoning `<select>` with the same legacy-value fallback. Both are `disabled={configSaving}`.
- **`packages/cluster/frontend/src/components/agent-settings/OverviewPanel.test.tsx`** — reshaped the `setup` helper to take a `SetupOpts` object, added a `makeClaudeCatalog` fixture, and added a 7-test `describe('model / reasoning dropdowns')` block covering: catalog-driven option count + preselect, model change fires `{ model, model_set: true }`, reasoning change fires `{ reasoning_effort, reasoning_effort_set: true }`, Default → `model: null`, legacy value renders as a disabled option, catalog=null hides the rows, rejected PUT surfaces inline error.
- **`packages/cluster/frontend/src/components/AgentSettingsDialog.test.tsx`** — added a `fetchEngineCatalog: vi.fn().mockResolvedValue(null)` mock to the shared `setup` so the existing 4 suite tests still assert section presence without involving dropdown rendering.

Verification:

- `npm run build` (tsc + vite) — clean.
- `npx vitest run` (frontend) — 26 files / 277 tests pass, including 16 in the Overview suite (9 existing + 7 new).
- `uv run pytest tests/test_agents_api.py tests/test_engine_catalog.py -q` — 44 pass (no backend regressions even though no backend code changed).

## Decisions

Three calls shaped the patch; rationale in `.tmp/plan-217-overview-model-dropdowns.md` §3.2.

1. **Overview section, not Manifest, not a new "Config" section.**
   - Options: (A) extend Overview inline; (B) add to Manifest; (C) spin out a new section.
   - Tipped the scale: Engine is already a read-only row in the Overview metadata grid; Model + Reasoning are Engine's dependent attributes, so landing them in the same `<dl>` matches the existing information shape.
   - Rejected: Manifest mixes two save models in one panel (its own bulk "Save" button vs. OverviewPanel's pick-commit) — cramming dropdowns in there would split the user's mental model mid-card. A fifth section would undo the section-count reduction #158/#165 explicitly targeted.
   - Assumption: Engine itself remains non-editable. If #73 (runtime editing) ever lets engine change too, the `useEffect` keyed on `agent.engine` already re-loads the catalog, so the dropdowns would follow without new work.

2. **Inline-duplicate the catalog-loading / reasoning-narrowing logic, don't extract a `useEngineCatalog` hook.**
   - Options: (A) extract a shared hook used by both AdminMachines and OverviewPanel; (B) copy the ~20 lines; (C) defer extraction until a third call site appears.
   - Tipped the scale: the two call sites' reset rules differ — the create dialog resets model + reasoning whenever the engine picker changes, the edit dialog doesn't need reset logic at all because engine is locked. Pulling the shared bits into a hook forces option flags or conditional early-returns that pollute the hook with knowledge of both callers.
   - Rejected: (A) is the classic pre-abstraction trap where the "shared" shape is defined by the first two callers and breaks when a third shows up.
   - Assumption: if a third call site emerges (batch editing UI, per-room agent overrides), we revisit and extract from three concrete shapes rather than two.

3. **Auto-commit on change, no Save button, no confirmation modal.**
   - Options: (A) `onChange` fires `updateAgent`; (B) drafts + Save button; (C) confirmation modal ("respawn agent with new model?").
   - Tipped the scale: the sibling controls in the same card (name blur-commit, avatar pick-commit) are already pick-commit. Introducing a Save button for this one field would break the card's unified commit pattern that #158/#165 just consolidated.
   - Rejected: (C) was scoped down — a stray change reverts by picking the previous value again, and `configSaving` already guards against double-click races on the select itself. The failure mode "admin fat-fingers a model change" produces one respawn, not data loss.
   - Assumption worth flagging: Claude Code's adapter (`claude_code.py`) currently ignores `reasoning_effort` entirely, so changing that dropdown on a Claude Code agent persists to DB but won't affect the next spawn until that adapter is extended. The plan marks this as an explicit out-of-scope follow-up; a future PR that wires `--effort` through must also decide whether to add a one-shot normalization for legacy `reasoning_effort` values.

## Result

- Admin → Machines → Settings on any existing agent now shows Model + Reasoning dropdowns directly below Engine. Selecting a new value auto-saves via `updateAgent`, which bumps generation and triggers a respawn with the new config (backend behavior unchanged since #6).
- Legacy values (e.g. a `model` no longer in the refreshed catalog) render as disabled `Current: X (no longer in catalog)` options so admins can see what's actually stored without the UI silently collapsing to "Default".
- Catalog-fetch failure, or an engine absent from the catalog, hides both rows — no empty `<select>` flash, no client error.
- 277 / 277 frontend tests pass (7 new for this feature); 44 / 44 backend tests pass.
- Follow-ups tracked separately: (1) wire `reasoning_effort` through the Claude Code adapter, (2) add a `codex-api-key` engine entry to expose the ChatGPT-auth-incompatible Codex variants (5.4-pro, 5.2-codex, 5.1-codex-max/mini), (3) one-shot DB normalization for legacy effort values once (1) lands.

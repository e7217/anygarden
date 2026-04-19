# feat(agents): per-agent context_window_opt_out (#148 Part 2)

- Commit: (pending — see PR)
- Author: Changyong Um
- Date: 2026-04-19
- PR: follow-up to Part 1 (#149)

## Situation

Part 1 (#149) promoted ambient context sharing to a per-**room** DB flag. But a single axis is not enough: an expensive or noisy agent (e.g. a Gemini pro-tier bot) needs a way to skip ambient ingestion even in a room where every other agent wants it on. The Stage B env knob was machine-wide, so this "per-agent override" was impossible. Part 2 adds the second axis — a per-agent **opt-out** — so the final policy is a clean AND: *room on AND agent not opt-out → ingest_only*.

## Task

- New `agents.context_window_opt_out BOOLEAN NOT NULL DEFAULT FALSE` column (migration 023).
- Expose the flag on `AgentOut` (`GET/POST/PUT /api/v1/agents/*`) and `MachineAgentOut` (`GET /api/v1/machines/{id}/agents`) so the AdminMachines detail list can draw the toggle without a second lookup.
- Accept the flag on `AgentUpdate` via the established `<field>_set` idiom so a rename PATCH can't silently reset the opt-out.
- Count the toggle as a non-avatar change so `bump_generation` respawns the agent; Part 3 wires the spawn-time read of the setting.
- Render an inline toggle item inside `AgentSettingsMenu` (not a new dialog) with check-mark semantics, wire it into both AdminMachines call sites (per-row list + per-machine detail) and the Sidebar DM list.
- Backend pytest + frontend vitest coverage for every new surface.
- **No behaviour change on broadcasts yet** — Part 3 wires the `decide_policy` branch.

## Action

### Storage
- `packages/cluster/doorae/db/migrations/versions/023_agent_context_opt_out.py` — batch_alter_table with `server_default=sa.text("0")` for SQLite NOT NULL + backfill. Mirrors migration 017 avatar patch.
- `db/models.py::Agent` — `context_window_opt_out: Mapped[bool]` with `server_default=sa_text("0")`, default False.

### API
- `api/v1/agents.py::AgentUpdate` — `context_window_opt_out: Optional[bool]` + `context_window_opt_out_set: bool = False`. `update_agent` applies only when `_set=True` and counts it as `non_avatar_changed` → `bump_generation` fires.
- `api/v1/agents.py::AgentOut` — field added, default False.
- `api/v1/machines.py::MachineAgentOut` + `list_machine_agents` — field added so the AdminMachines detail table row sees it without fetching `/agents/{id}`.

### Frontend types
- `hooks/useAgents.ts::Agent` — `context_window_opt_out?: boolean`. `updateAgent` patch accepts the `_set` pair.

### UI
- `components/AgentSettingsMenu.tsx` — new props `contextWindowOptOut` + `onToggleContextWindowOptOut` (paired; both required to render). Toggle item uses `role="menuitemcheckbox"`, `aria-checked`, `EyeOff` icon, trailing `Check` glyph when on, label "대화 맥락 공유 제외". Sits between the safe-action group and the destructive `Delete agent` row with appropriate dividers.
- `components/AdminMachines.tsx` — `handleToggleContextWindowOptOut(agentId, current)` flips the flag via `updateAgent`; both `AgentSettingsMenu` call sites pass it in. MachineAgent TS interface extended to mirror backend.
- `components/Sidebar.tsx::AgentDMListAdmin` — same pattern for the DM-list agent row.

### Tests
- `tests/test_agents_api.py::TestAgentManifestAPI::test_update_agent_context_window_opt_out_toggle` — POST→defaults False, PUT with `_set=True` flips to True and persists, rename PATCH without the flag leaves the opt-out intact, generation bump verified in-DB.
- `tests/test_migrations.py` — `"022"` → `"023"` head revision bumped in all five assertion sites.
- `frontend/src/components/AgentSettingsMenu.test.tsx` — adds a "context window opt-out toggle" describe block: (a) renders only when both props present, (b) hidden when either is omitted, (c) aria-checked=false when flag off, (d) aria-checked=true when flag on, (e) click invokes handler and closes menu.

## Decisions

### Where does the toggle live? — `AgentSettingsMenu` item vs. new dialog vs. file-manifest dialog
- A. New per-agent "Settings" dialog → **over-scoped**. Only one new field; extra round-trip for a click.
- B. Extend `AgentEditDialog` (file manifest) → **scope bleed**. That dialog is file-tree UX; a 1-bit toggle at the top would noise it up.
- C. Inline toggle item inside `AgentSettingsMenu` dropdown with a trailing check mark → **chosen**.

Rationale: plan §3.2 decision 6 already argued "extend existing over new dialog." The menu is the only shared agent admin surface across AdminMachines (×2 call sites) and Sidebar; placing the toggle there makes every admin entry-point consistent and avoids Yet Another Dialog. `menuitemcheckbox` + `aria-checked` gives the right ARIA semantics for a toggle inside a menu (same pattern VS Code uses for "Toggle Word Wrap").

### `_set` idiom for the PATCH body
- A. Plain `context_window_opt_out: bool` — every PATCH carries a default False and resets unless the caller explicitly echoes the current value.
- B. `Optional[bool]` + `context_window_opt_out_set: bool` → **chosen**.

Rationale: every other AgentUpdate field already uses `_set` (`agents_md_set`, `avatar_kind_set`, …). Matching the pattern keeps the schema uniform and prevents surprise regressions when a future surface only wants to edit the name.

### Counting the toggle as `non_avatar_changed` (⇒ bump_generation)
- A. Skip the bump, pure UI change. 
- B. Bump, so the agent respawns and picks up the new setting → **chosen**.

Rationale: this IS a policy change, not UI metadata. The alternative would make the flag "true in the DB but still ingesting in memory" until the agent happens to restart for another reason, which matches nothing a user would expect. Part 3 will read `context_window_opt_out` at spawn time; bumping now is what makes the toggle "click it and it takes effect on the next live-cycle restart."

### Toggle state lives in parent (React state), menu is stateless
- A. Internal toggle state in `AgentSettingsMenu` that later syncs upward.
- B. Controlled toggle — `contextWindowOptOut` prop + handler → **chosen**.

Rationale: `updateAgent` already triggers `fetchAgents`, so the parent's source of truth refreshes after each click. Keeping the menu purely presentational means optimistic rollback / retry / error boundaries all live at the AdminMachines / Sidebar layer where they already exist for delete. Mirrors the menu's existing "callback-in, render-pure" shape — no local state added.

### `MachineAgentOut` needs the field too (not just `AgentOut`)
- AdminMachines renders two agent lists: the cluster-wide admin list (via `useAgents()` / `AgentOut`) AND the per-machine detail view (via `list_machine_agents` / `MachineAgentOut`). If only `AgentOut` carried the flag, the per-machine detail's check mark would always be stale or require a second lookup per row. Adding one field to `MachineAgentOut` is cheaper than double-fetching, and the machines router already serializes directly from the Agent ORM object so the change is a single line.

## Result

- agent-side ambient opt-out is persisted, exposed on both admin REST surfaces, and toggle-able from the three places admins already manage agents (AdminMachines list, AdminMachines detail, Sidebar DM list).
- cluster pytest: 582 passed (1 new opt-out test, 5 migration-head bumps). No regressions.
- frontend vitest: 223 passed (5 new opt-out cases in `AgentSettingsMenu.test.tsx`). `npm run build` clean.
- agent + machine tests: 171 + 232 passed (OpenAI test skipped; same pre-existing env requirement as on main).
- **No runtime behaviour change yet.** The flag is stored and visible; Part 3 will make `decide_policy` honour it so `ingest_only` broadcasts actually SKIP on opt-out agents. Until then, operators can pre-set the flag on sensitive agents and it will "light up" automatically when Part 3 merges.

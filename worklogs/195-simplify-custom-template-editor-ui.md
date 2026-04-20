# refactor(mcp-templates): simplify custom template editor UI (#195)

- Commit: `3407410` (34074107e43e21fed32beeea25c2b20b6178609a)
- Author: Changyong Um
- Date: 2026-04-20T15:31:26+09:00
- PR: #195

## Situation

The admin-facing `CustomEditorDialog` in `packages/cluster/frontend/src/components/AdminMCPTemplates.tsx` forced admins to hand-assemble every piece of an MCP server registration: separate `slug` and `display_name` fields, three engine checkboxes, a raw `config_per_engine` JSON textarea spanning `{"claude-code": …, "codex": …, "gemini-cli": …}`, and a comma-separated `required_env_vars` input that duplicated the placeholder keys already present in `config.env`. Every built-in template (github, slack, notion, linear, filesystem) goes through the `_three_engine_stdio` helper, so the per-engine split was never exercised in practice while still paying the UX cost. A single JSON typo would destroy the whole `config_per_engine` payload, and the error message was buried inside an internal scroll container so long inputs hid it below the fold.

## Task

- Collapse the editor down to the minimum fields an admin needs for a stdio MCP server: display name, command, args, env rows.
- Derive the slug, per-engine fan-out, and `required_env_vars` automatically from the simplified form.
- Keep the existing per-engine JSON editor reachable as an Advanced fallback so genuinely divergent templates (e.g. HTTP transport, engine-specific overrides) are not lost.
- Leave the backend API contract, DB schema, and built-in seed untouched.
- Move the error text out of the scroll region so it is always visible above `DialogFooter`.

## Action

- Added `packages/cluster/frontend/src/lib/mcpTemplateForm.ts` with four pure helpers — `slugify`, `extractPlaceholders`, `buildTemplatePayload`, and `parseTemplateIntoForm` — plus shared type definitions (`TemplateFormState`, `EnvRow`, `ApiPayload`, `StdioConfig`, `ParsedTemplate`). `buildTemplatePayload` fans one stdio block out to all three engines; `parseTemplateIntoForm` inspects the three engine blocks for identical JSON and an exact stdio key set (`command`/`args`/`env`) before picking `simple` mode, otherwise returning `{ mode: 'advanced' }`.
- Added `packages/cluster/frontend/src/lib/mcpTemplateForm.test.ts` covering 25 Vitest cases across all four helpers (slug fallbacks, placeholder merging from args + secret env rows, `required_env_vars` dedup, advanced-mode detection for divergent configs, non-stdio keys, missing engines, and plain vs. secret env parsing).
- Rewrote `CustomEditorDialog` in `AdminMCPTemplates.tsx`:
  - Two render branches driven by a `mode: 'simple' | 'advanced'` state. The simple branch exposes `displayName`, auto-derived `slug` (`slugify` re-runs via `useEffect` until the user edits the slug), description, a fixed `stdio` transport badge, `command`, dynamic args rows with per-row remove buttons, env rows with a per-row `Secret` checkbox (hides the value input when set), and a read-only chip list of the `required_env_vars` that `extractPlaceholders` discovered.
  - The advanced branch restores the previous `slug` + `display_name` + `description` + `supported_engines` checkboxes + `required_env_vars` comma input + `config_per_engine` JSON textarea, and is entered automatically when Edit-mode `parseTemplateIntoForm` returns advanced, or manually via the `Advanced ▸ / ◂ Simple` toggle in `DialogFooter`.
  - Mode-switching tries `JSON.parse` + `parseTemplateIntoForm` before returning to simple; if the result is advanced-only it surfaces an inline error and stays in advanced. Simple→advanced seeds the textarea by running the current form through `buildTemplatePayload` so the JSON view is immediately editable.
  - `handleSave` delegates payload construction to either `buildTemplatePayload` (simple) or the advanced state, then on Create-mode `409 Conflict` retries with `-2`/`-3` suffixes up to three attempts before surfacing the detail with an "or set a custom slug in Advanced mode" hint.
  - Error text now lives between the scroll container and `DialogFooter`, so it never scrolls out of view.
- Exposed `SUPPORTED_ENGINE_IDS` from the new utility instead of depending on the local `SUPPORTED_ENGINES` constant for the fan-out path, while keeping the latter for the Advanced engine-checkbox UI.

## Decisions

Sourced from `.tmp/plan-195-mcp-template-editor-simplify.md`, the linked issue body, and the existing MCP-template modules.

- **Frontend-only fan-out vs. a new backend endpoint or a `simplified=true` flag.** The plan flagged three options. Introducing a second `POST /simplified` endpoint would fork the schema and force Edit/List to still understand the old shape for builtin rows. A `simplified` flag on the current endpoint would push the disjoint union into `MCPTemplateCreate`/`MCPTemplateUpdate` and weaken validation. Fan-out in the browser keeps `mcp_templates.service._validate_config` happy with no changes because it already walks the dict structure per engine; the UI just always hands it three identical blocks. Rejected the backend paths so this PR stays UI-only and the API stays frozen. The assumption that carries the decision: all three supported engines keep using the `{command, args, env}` stdio shape. When Claude Code 2.x or similar starts requiring HTTP transport metadata, the Advanced mode already covers it; if that becomes common enough to deserve first-class UI, re-open this decision.
- **Secret checkbox + `${VAR}` scan vs. keeping the comma-separated `required_env_vars` input.** Option B in the plan kept a separate placeholder field for args-only cases like filesystem; option C kept the current input entirely. Both break the "minimum fields" goal — filesystem is the single outlier and `merge.py`'s `_PLACEHOLDER_RE` already matches across args and env recursively, so a one-line regex in the UI produces the exact same set the runtime would resolve. Chose A: secret env rows emit `${KEY}` into the config automatically, the args array is scanned for the same pattern, and the two sets are unioned into `required_env_vars`. Assumes admins understand `${VAR}` syntax — the filesystem builtin already surfaces it, so this is consistent with existing UX.
- **Auto-detecting simple vs. advanced on Edit vs. forcing simple everywhere.** Forcing conversion would silently destroy engine-specific divergence for any custom template an admin might have crafted via the old JSON UI. Prompting the user each time was judged pure overhead when ~99% of rows already satisfy the simple predicate. Went with auto-detect by comparing all three engine blocks with `JSON.stringify` and checking the key set is exactly `{command, args, env}`; anything else falls to advanced so data is preserved. The Advanced UI itself was left in place rather than redesigned — that is out of scope for this issue.
- **Slug collision handling in the client (`-2` retry) vs. server-side suffixing vs. pre-flight GET.** Pre-flight lookups race between concurrent admins and still need a server tiebreaker; server-side suffixing would be a backend change. The existing `TemplateNameConflict` → `409` path at `service.py:162-168` and `api/v1/mcp_templates.py:209` is already the source of truth, so the client retries up to three times with `-2`/`-3` suffixes and surfaces a descriptive error (pointing at Advanced mode for manual slug entry) if it still fails. Three attempts is the "four admins creating the same display name simultaneously" threshold — unrealistic in practice.

## Result

- Admins registering stdio templates now see a single-page form with no JSON, slug derivation, secret toggle, and an auto-derived placeholder preview; the backend payload is byte-identical to what the current admin hand-assembles.
- `packages/cluster/frontend/src/lib/mcpTemplateForm.test.ts` adds 25 Vitest cases; `npx vitest run` is green across all 270 frontend tests, `npx tsc -b --force` exits 0, and `NODE_OPTIONS=--max-old-space-size=4096 npm run build` produces static assets in 1m42s.
- `uv run pytest tests/test_mcp_templates_crud.py tests/test_mcp_templates_lifecycle.py tests/test_mcp_templates_builtin_seed.py tests/test_mcp_templates_merge.py` passes all 42 tests, confirming the API/DB/seed contract is untouched.
- Edit-mode divergent templates still load into the Advanced JSON textarea; slug conflicts retry automatically on Create; the error row now sits between the scroll container and `DialogFooter` and stays visible with long forms.
- Follow-ups left open (separate issues per the plan): first-class HTTP transport UI, and a proper frontend Playwright/Vitest E2E suite — this PR still relies on the manual checklist in the plan's §4 Step 7 for UI regression.

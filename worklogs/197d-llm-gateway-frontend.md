# feat(llm-gateway): admin frontend for the embedded gateway (#197 Phase 4)

- Commit: `b19c2b7` (b19c2b76ea1e408e2e0e6c5b34dc3e5e4ea60e50)
- Author: Changyong Um
- Date: 2026-04-20T22:05:50+09:00
- PR: (pending)

## Situation

The gateway backend has been live in Phase 2 (#202) with supervision
+ reverse proxy + bootstrap, and Phase 3 (#203) added 12 admin REST
endpoints. What's missing: a way for a human admin to actually
register models, rotate API keys, and apply changes without
POSTing JSON by hand. Phase 5 (agent wiring) blocks on this because
the operator needs to set up a working config before flipping the
feature flag.

## Task

Ship the frontend surface sketched in §12.4 of the design doc:

- Secondary-sidebar layout with 4 nav sections + a fixed Apply
  footer that's reachable from any section.
- Models / Secrets / Status / Usage sections matching the admin
  workflows: add → edit → test → apply.
- No new dependencies — stay on the project's existing fetch-based
  hook pattern (useMachines/useAgents style) rather than bringing
  in React Query just for this page.
- Follow DESIGN.md: warm neutral palette, whisper borders, sub-0.05
  shadows, Notion Blue accent only for primary actions. State tints
  at low opacity so status colour doesn't fight the content.

Non-goals: per-secret Test button (stubbed; per-model Test already
covers the same verification path), cost estimation (deferred to
Phase 5 when pricing data lands), WebSocket-backed live status
(polling every 5s is enough for the current scale).

## Action

**Hooks** (`src/hooks/useLLMGateway.ts`)
- Four independent hooks: `useGatewayModels`, `useGatewaySecrets`,
  `useGatewayStatus(pollMs)`, `useGatewayUsage(window)`.
- Each follows the existing `useMachines` convention —
  `{data, status, error, refresh, <mutations>}` shape with
  optimistic local updates before background refetch.
- `useGatewayStatus` supports an optional polling interval (shell
  polls 10s, Status section polls 5s — the hook cleans up its
  interval on unmount).
- `readErrorDetail` helper unwraps FastAPI's `{detail: ...}`
  envelope into user-facing error messages.

**Shell** (`src/pages/AdminLLMGatewayPage.tsx`)
- Three-column layout: main Sidebar → SecondarySidebar → `<Outlet/>`
  section content.
- Owns `pendingCount`, `applying`, `applyBump` state passed to
  sections via `useOutletContext`. `incrementPending()` is called
  by any mutation that changes DB state that isn't yet reflected in
  the running subprocess; Apply resets it to 0.
- Pre-warms the models/secrets hooks at mount so navigating into
  those sections feels instant.

**Secondary sidebar** (`components/admin-llm-gateway/SecondarySidebar.tsx`)
- 4 nav items grouped under "Configuration" (Models/Secrets) and
  "Runtime" (Status/Usage), plus a status dot next to "Status"
  that reflects the current state (green/blue/amber/red).
- Bottom-fixed footer with state label + pending count + the
  Apply button. Button is disabled when pending=0 or a mutation
  is in flight; shows a spinner while applying.

**Sections**
- **ModelsSection** — card list. Each card shows enabled toggle,
  name, provider, upstream id, key reference, last test result
  (inline, not stored). Inline Test runs the per-model ping
  exposed by Phase 3's `/models/{id}/test`. Edit/Add reuse the
  same ModelDialog.
- **ModelDialog** — provider dropdown (Anthropic/OpenAI/Bedrock/
  Vertex/Azure/Ollama/Custom) prefills the upstream prefix so
  admins don't have to remember `anthropic/...`. API key
  selector pulls from the existing Secrets table; falls back to a
  free-text input when no secrets are registered yet so the empty
  state has a path forward.
- **SecretsSection** — card list. Each card shows env var name,
  masked value preview (from the server's `value_preview` field —
  the frontend never handles plaintext after the create/edit
  submit), last test badge (Valid / Not tested / error status).
- **SecretDialog** — env var name is locked on edit (referenced by
  model rows). Value field is `type=password` with
  `autocomplete=off` so browser fill doesn't surface the value.
- **StatusSection** — state-tinted card with a summary + full
  attribute list (PID, port, config hash, crash count, last
  error). Apply button duplicates the footer's action; Hard
  restart is a separate button with a confirm dialog since it's
  the "recovery from FAILED" escape hatch. Polls `/status` every
  5s via the hook's `pollMs` parameter.
- **UsageSection** — 3 summary cards (requests / tokens / cost),
  horizontal bars for by_model (normalized to the top model's
  count, min 1.5% so the smallest bar is still visible), plain
  list for top-5 by_agent. Cost card shows '$—' with a "Pricing
  data coming in Phase 5" hint until the pricing table lands.

**Wiring** (`src/App.tsx`, `src/components/Sidebar.tsx`)
- Nested route under `/admin/llm-gateway` with an index redirect
  to `/models` so the shell always has a concrete section.
- `AdminRoute` gating (same as the other admin pages).
- Sidebar admin section gains a "LLM Gateway" entry (Waypoints
  icon, active state matches any `/admin/llm-gateway/*` path).

## Result

- Frontend typecheck clean: `tsc -b` passes.
- Production bundle builds (vite): ~922 KB main / 257 KB gzipped —
  +~15 KB over previous baseline for the new sections. No new
  dependencies; reuses existing shadcn/ui components (Dialog,
  Button, Input, Label) plus lucide-react icons already in the
  bundle.
- All four sections respect DESIGN.md: cards use whisper border +
  sub-0.05 shadow, state tints ride the existing emerald/blue/
  amber/red families with 50-60 alpha backgrounds, Notion Blue
  (`--color-accent`) is reserved for the primary Apply + Save
  actions and the usage bars.
- No backend changes — the 12 REST endpoints from Phase 3 are
  consumed as-is.
- Phase 5 (agent wiring) is unblocked: an operator can now add a
  secret, register a model, test it end-to-end, and Apply the
  configuration so the running subprocess actually serves that
  model.

## What's next

Phase 5: `claude_code.py` / `codex.py` adapters wrapped with
`secrets_in_env([...])`, manifest builder injecting
`ANTHROPIC_BASE_URL` / `OPENAI_BASE_URL` + `*_AUTH_TOKEN` when the
flag is on and the per-agent toggle opts in. With that merged, a
B-machine scenario (no internet, reachable only through
doorae-server) will work end-to-end.

Follow-up polish (not blocking Phase 5):
- Per-secret Test button (enable once the backend adds
  `/secrets/{env_var_name}/test` or we pick the first model that
  references the secret).
- WebSocket-backed live status instead of 5s polling.
- Cost estimation (requires a pricing table keyed by
  `upstream_model`; could land with Phase 5 or later).

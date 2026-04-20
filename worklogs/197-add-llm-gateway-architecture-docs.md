# docs(llm-gateway): add ADR-004 and design doc for embedded LiteLLM gateway (#197)

- Commit: `02404a2` (02404a24002713af5a0474cd12eb356d6b34f5de)
- Author: Changyong Um
- Date: 2026-04-20T20:01:58+09:00
- PR: #197

## Situation

Agents in doorae call LLM APIs (Anthropic, OpenAI, …) directly from the
machine where they are spawned — the `doorae-machine` daemon inherits
the operator's host-level env and passes it through to the agent
subprocess. This works for a single internet-connected host but breaks
down in three ways: (a) agents on a machine without outbound internet
cannot reach `api.anthropic.com`; (b) there is no way to track per-room
or per-agent LLM usage because the calls never traverse doorae; and (c)
as soon as multiple engines (Claude Code + Codex) share a machine, each
one speaks a different upstream protocol, so a plain reverse proxy
cannot cover both.

We needed a design decision on how to introduce an LLM gateway **before**
writing any code, so the architecture could be reviewed on its own and
the later implementation PRs would not mix "what" with "how".

## Task

Produce the Phase 1 documentation bundle for issue #197:

- A decision record that is self-contained enough to justify the choice
  to any future reader without them having to read the design doc.
- A full design chapter that shows the data flow (agent → `/api/v1/llm/*`
  → LiteLLM subprocess → upstream), the supervisor state machine, the
  config drafting / Apply pattern, the admin UI layout, and Phase 5
  agent-side wiring.
- Update the existing topology diagram in `§01-architecture.md` so the
  LiteLLM subprocess and the `/api/v1/llm/*` reverse-proxy edge are
  visible at a glance, including a note that the feature is opt-in via
  `DOORAE_LLM_GATEWAY_ENABLED`.
- Keep this PR docs-only — no code, no Alembic, no package changes — so
  the subsequent implementation phases can land as independent PRs.

## Action

**New ADR** (`docs/decisions/004-embedded-litellm-gateway.md`):

- Spelled out the three operational gaps (B-machine scenario, usage
  tracking, multi-protocol routing) and mapped them to the four
  candidate shapes (sidecar / `app.mount` / subprocess / Caddy-only).
- Chose subprocess-managed-by-doorae for deployment simplicity
  (artifact stays singular), operational coupling (`lifespan` owns it),
  and SQLite preservation (LiteLLM runs stateless, no Postgres).
- Captured the stance on `uv tool install` vs `uvx`, always-on vs
  lazy-start, and draft-Apply vs immediate-apply explicitly under
  "Alternatives considered" so those choices don't have to be
  re-litigated in implementation reviews.

**New design chapter** (`docs/design/12-llm-gateway.md`):

- Section 12.0 summary and principles (subprocess only, stateless,
  `127.0.0.1`-only listen, draft-Apply, feature-flag off preserves the
  current path).
- 12.1 data flow with a mermaid sequence diagram from agent env
  (`ANTHROPIC_BASE_URL=<server>/api/v1/llm`) through the reverse-proxy
  header swap to the upstream response, plus the usage-logging tap.
- 12.2 supervisor state machine (`INIT → STARTING → RUNNING → CRASHED
  → STARTING` / `RUNNING → RESTARTING → STOPPED → STARTING` /
  `FAILED`), including backoff (`[1s, 5s, 30s]`) and graceful-shutdown
  timing (SIGTERM → 30 s grace → SIGKILL).
- 12.3 draft-Apply configuration pattern, including the invariant that
  secrets never land in `litellm.yaml` — only `os.environ/…`
  references, with values injected via `env=` at spawn time using the
  existing `DOORAE_MCP_SECRETS_KEY` (Fernet).
- 12.4 admin UI with a secondary-sidebar layout (Models / Secrets /
  Status / Usage + fixed Apply footer) and the permission boundary
  (`get_admin_identity` for `/api/v1/llm-gateway/*`, but the reverse
  proxy `/api/v1/llm/*` accepts any authenticated identity so agents
  can actually call it).
- 12.5 deployment (`uv tool install 'litellm[proxy]'` in the Makefile,
  new `DOORAE_LLM_GATEWAY_*` env vars, daily TTL cron for the usage
  table).
- 12.6 agent wiring (`secrets_in_env` gap in `integrations/claude_code
  .py` and `integrations/codex.py`) and the manifest builder change
  that adds `ANTHROPIC_BASE_URL` / `OPENAI_BASE_URL` +
  `*_AUTH_TOKEN=<agent doorae token>` when the feature flag and the
  per-agent toggle are both on.
- 12.7 threat model (reverse-proxy bypass impossible, token theft
  downgrades to per-agent scope, secret leakage bounded by the same
  KMS surface as MCP secrets).

**Architecture diagram update** (`docs/design/01-architecture.md`):

- Added `LLM_GW["litellm subprocess · 127.0.0.1:4001"]` inside the
  `ServerHost` subgraph, with `APP -.->|lifespan supervise| LLM_GW`
  and `REST -.->|/api/v1/llm/* reverse proxy| LLM_GW` edges.
- Added an `LLMProv` subgraph for the external providers (Anthropic,
  OpenAI, Bedrock/Vertex) and wired `LLM_GW` to all three as the
  "gateway path".
- Kept the existing direct edges from agents to providers, relabelled
  as the "기본 · LLM 직접 호출" path to make the two options legible.
- Added observation point #5 explaining the feature-flag gate and the
  coexistence of the two paths, linking out to §12 and ADR-004.

## Result

- Three files shipped (2 new, 1 updated) — no Python, no TypeScript,
  no migrations.
- ADR-004 is self-standing: the `Alternatives considered` section
  documents **why not sidecar / not mount / not Caddy / not Postgres /
  not uvx / not lazy-start / not immediate-apply** so future reviewers
  do not have to re-derive those boundaries.
- Design chapter 12 is wired into the numbered `docs/design/` series
  and cross-linked from ADR-004 and from the topology section's new
  observation point; §10 Machine Scheduling is explicitly positioned
  as the base layer this chapter adds to, so the docs form a
  coherent stack instead of an orphan page.
- The topology diagram now reflects the architecture the subsequent
  Phase 2–5 PRs will materialize, without making the LLM gateway look
  mandatory (dashed "직접 호출" path is still the default and stays
  until a machine opts in).
- Phase 2 (supervisor + config writer + reverse proxy) can land next
  with a clear design contract; Phase 3 (admin API), Phase 4 (admin
  UI), and Phase 5 (agent wiring) follow as independent PRs per the
  rollout plan recorded in the issue.

# feat(gateway): wire engine_secrets + gateway model merge for OpenHands (Ollama path) (#359)

- Commit: `e496d68` (e496d686e1e6db9353f387a30ad1136a8ebb24cb)
- Author: Changyong Um
- Date: 2026-05-10
- PR: #359

## Situation

#355 added the OpenHands V1 SDK as a fourth engine (adapter +
catalog + DelegateTool + validation plan). #357 made the machine
detector advertise it. With both shipped, an operator could create
`oh-agent01` in the admin UI and the agent process would actually
boot — but sending it a chat message returned silence. Diagnosis:
`Conversation.run()` raised `litellm.AuthenticationError` because
the agent had no provider API key, and the adapter's `except
Exception` at the bottom of `on_message` swallowed it to `None`
return.

doorae had built infrastructure for exactly this scenario in
ADR 004 (#197): an embedded litellm proxy under
`/api/v1/llm/*` that agents talk to, with admin-managed model
registry in `llm_gateway_models`. The supervisor / config_writer /
reverse_proxy were all implemented and the operator's local Ollama
(`qwen3.6:27b`) was already in the gateway DB. Two gaps remained
unimplemented from #197 Phase 5 — without them the gateway was
"there" but agents couldn't reach it:

1. `lifecycle.py:686` hardcoded `engine_secrets={}` — the spawn
   frame never carried the BASE_URL/auth-token pair the SDK needs
   to find the proxy.
2. `agents.py:get_engine_models` only returned the static catalog
   — the operator's `qwen3.6:27b` was invisible in the model
   dropdown, so admins had no way to pick it on the openhands
   engine.

## Task

Close those two gaps narrowly enough that the user-reported
"oh-agent01 is silent" goes away, without disturbing the three CLI
agents (`agent01-claude`, `agent01-codex`, `agent01-gemini`) that
are currently working via their own OAuth flows.

Constraints from the plan + brainstorming:
- The standard provider env names (`OPENAI_BASE_URL`,
  `ANTHROPIC_BASE_URL`, …) are read by every SDK that talks to
  those providers. Populating them universally would silently
  re-route claude-code/codex/gemini-cli through a gateway that
  has only Ollama registered → "model not found" → those agents
  break too. **Engine-scoped secrets** (only openhands sees them).
- The reverse proxy at `/api/v1/llm/*` validates any user / agent /
  machine token via `get_current_identity`. Reuse the existing
  `doorae_token` minted per-spawn for the doorae self-MCP entry
  rather than mint a second token.
- Pre-`DOORAE_LLM_GATEWAY_ENABLED=true` deployments and tests
  must be byte-identical to pre-#359 — the new code path is
  feature-flagged.

## Action

- `packages/cluster/doorae/scheduler/gateway_secrets.py` (new):
  - `build_engine_secrets(*, engine, gateway_enabled,
    cluster_external_url, agent_token)` — five guards, returns
    `{}` if any guard fails (`engine != "openhands"` first, then
    flag / URL / token presence). Happy path returns
    `{"OPENAI_BASE_URL": "<url>/api/v1/llm/v1",
       "OPENAI_API_KEY": <token>}`.
  - `openhands_model_id_for_gateway(provider, model_name)` —
    always returns `openai/<model_name>` because the doorae
    proxy is OpenAI-compat regardless of upstream provider.
- `packages/cluster/doorae/scheduler/lifecycle.py`:
  - `AgentLifecycle.__init__` grew `llm_gateway_enabled: bool =
    False` keyword arg (default keeps existing tests passing).
  - `_build_sync_frame` ensures `doorae_token` is minted for
    openhands when gateway is on and MCP didn't already mint
    (Phase 1 of plan). Token row lands in `agent_tokens` so the
    reverse proxy validates the same value the agent sends back.
  - Hardcoded `"engine_secrets": {}` (was line 686) now calls
    `build_engine_secrets(engine=agent.engine, ...)`.
- `packages/cluster/doorae/app.py:340`: passes
  `config.llm_gateway_enabled` through to `AgentLifecycle`.
- `packages/cluster/doorae/api/v1/agents.py:get_engine_models`:
  for `engine == "openhands"`, queries `LLMGatewayModel` (enabled
  rows), runs each through `openhands_model_id_for_gateway`, and
  appends to the response with `source="gateway"` and
  `reasoning_levels=[]`. Static-catalog ID collisions silently
  defer to the static entry. Other engines get the pre-#359
  catalog-only behaviour.
- `packages/cluster/tests/test_gateway_secrets_population.py`
  (new, 11 tests): helper matrix.
- `packages/cluster/tests/test_lifecycle_engine_secrets.py`
  (new, 8 tests): integration matrix on `_build_sync_frame`.
  Includes the CRITICAL regression guard:
  `claude-code/codex/gemini-cli + gateway-on → engine_secrets={}`.
- `packages/cluster/tests/test_engine_catalog.py`: 6 new tests
  (`TestOpenHandsGatewayMerge`) — gateway rows surface for
  openhands, static catalog still present, disabled rows
  filtered, other engines untouched, no-rows fallback.
- `docs/runbook/openhands-ollama-setup.md` (new): operator
  procedure with the two env vars, the (one-time) gateway DB
  check, restart, healthcheck via `curl /api/v1/llm/v1/models`,
  and a troubleshooting section keyed to specific error shapes.

## Decisions

The plan in `.tmp/plan-359-llm-gateway-ollama-openhands.md`
captured the design discussion that drove these choices. Three
substantive decisions:

### Decision 1: Scope of `engine_secrets` population (A vs B)

| Option | What it does |
|---|---|
| **A — engine == "openhands" guard** (chosen) | Only openhands gets the env keys; the three CLI engines stay byte-identical to pre-#359 |
| **B — populate for every engine** (ADR 004 original intent) | All four engines route through the gateway |

Initial draft was B. User pushed back when they realised the
implication: `ANTHROPIC_BASE_URL` etc. are SDK-wide standards, so
`agent01-claude` (claude-agent-sdk) would start sending requests
to the doorae gateway too. With only Ollama in the gateway DB,
that means `agent01-claude` immediately starts failing with
"model not found" — fixing oh-agent01 by breaking three working
agents is the wrong trade.

What tipped the scale: the user-reported pain is just oh-agent01.
A solves that without touching the other three. If the operator
later registers Anthropic / OpenAI / Google models in the gateway
DB and wants those engines on the gateway too, dropping the
engine guard is a one-line change in a follow-up — but landing it
*now*, with only Ollama registered, would be a regression.

Bonus: per the search results pulled during brainstorming, in
April 2026 Anthropic blocked third-party harnesses from using
Claude Max subscription limits, so even if the operator did
register Anthropic in the gateway later, `agent01-claude`'s
existing claude.ai OAuth path may not be replaceable without
side effects. Worth re-evaluating empirically before flipping.

### Decision 2: Token reuse vs separate LLM token

Reuse the per-spawn `doorae_token` already minted for the doorae
self-MCP entry (line 642 of lifecycle.py). The reverse proxy's
`get_current_identity` accepts agent tokens, so a second token
would buy nothing but extra rows in `agent_tokens` and a longer
spawn frame.

Rejected alternative: scope-bound tokens (one for MCP, one for
LLM). Conceptually cleaner but doorae's security boundary
already trusts the agent process, so no real privilege gain.
Filed away for a future multi-tenant scenario.

### Decision 3: Merge gateway models into static catalog (vs replace)

Append `source="gateway"` rows alongside the static `source="builtin"`
rows. The `EngineModelOut.source` field was already in the schema
with a docstring referencing this exact distinction — #359
finally fills it in.

Static-catalog wins on ID collision because operators who have
external API keys still see the curated entries. The one anomaly
this preserves: `openai/qwen3.6:27b` gateway-rendered would
collide with a static `openai/qwen3.6:27b` if someone added one,
but no such static entry exists today, so the rule is theoretical.

### Assumptions worth flagging

- The gateway proxy continues to expose OpenAI-compat at
  `/api/v1/llm/v1`. If it ever moves to `/api/v1/llm/openai/v1`
  or some such, `build_engine_secrets` and the runbook's
  `curl /v1/models` healthcheck both need updating.
- `get_current_identity` will keep accepting agent tokens at
  `/api/v1/llm/*`. If a future hardening pass scopes that
  endpoint to user JWTs only, the agent's `OPENAI_API_KEY` value
  stops working and we'd need a dedicated LLM-scope token.
- Phase 0/1 ships OPENAI_* only. ANTHROPIC_* / GEMINI_* keys
  are absent because the proxy serves OpenAI-compat for every
  upstream; if a future change moves to per-protocol routing,
  the helper needs to learn additional env keys.

## Result

oh-agent01 path is now end-to-end: with
`DOORAE_LLM_GATEWAY_ENABLED=true` + `DOORAE_CLUSTER_EXTERNAL_URL`
set, the spawn frame carries `OPENAI_BASE_URL` +
`OPENAI_API_KEY`, the OpenHands SDK routes through the doorae
proxy, the proxy validates the agent token, litellm forwards to
ollama, and the response flows back. The runbook walks an
operator through the activation steps + healthchecks +
troubleshooting.

Coverage: 947 / 947 cluster tests pass (was 915 pre-#359). All
four engine paths exercised in
`test_lifecycle_engine_secrets.py`. `ruff check` clean on
changed files.

Pending follow-ups (separate issues):
- Extend `engine_secrets` to claude-code / codex / gemini-cli
  once the operator has matching upstream models registered and
  validated. The engine guard in `gateway_secrets.py` is the
  exact place to flip.
- Surface gateway-registered models for engines other than
  openhands once the routing actually works for them.
- Operator-side: register Anthropic / OpenAI / Gemini API keys
  in the gateway DB if they want non-Ollama upstream models.

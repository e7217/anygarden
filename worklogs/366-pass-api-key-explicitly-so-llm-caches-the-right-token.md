# fix(agent/openhands): pass api_key explicitly so LLM caches the right token (#366)

- Commit: `4c6d470`
- Author: Changyong Um
- Date: 2026-05-10
- PR: #366

## Situation

#364 fixed the supervisor's PATH-shadowing trap, so the LLM
gateway finally reached `state=running` and
`/api/v1/llm-gateway/models/{id}/test` returned 200 OK. But
`oh-agent04` was *still* silent on every chat. The server log
showed:

    INFO: 127.0.0.1:39352 - "POST /api/v1/llm/v1/chat/completions
                          HTTP/1.1" 401 Unauthorized

The agent reached the doorae reverse proxy (so
`OPENAI_BASE_URL` was wired in correctly) but presented no
Bearer token. doorae's auth middleware rejected with 401 — same
endpoint that was 200 for the operator-triggered model test, so
the gateway path itself was healthy. The break lived
specifically in how the agent presented credentials at request
time.

## Task

Find why agent's gateway request carries no token despite
`engine_secrets` being correctly populated (`OPENAI_API_KEY`
matches the same `doorae_token` the .mcp.json file uses, which
validates fine on `/mcp/rpc`).

Constraints:
- Pre-#366 deployments without engine_secrets in agent_secrets
  must keep working (preserve SDK auto-discovery — operators
  using AWS Bedrock IAM, Vertex ADC, or env-passed keys some
  other way shouldn't suddenly receive an empty `api_key=""`).
- Tests must lock the regression so a future refactor that
  drops the explicit kwarg pass-through resurfaces the same
  silent 401.

## Action

Diagnostic: `grep`'d `openhands.sdk.llm.LLM` source. Found
`api_key: str | SecretStr | None = Field(...)` is a Pydantic
field — frozen at construction. Pre-#366 the adapter built the
LLM with no `api_key` kwarg and counted on the
`secrets_in_env` context manager wrapping `Conversation.run`
to populate `OPENAI_API_KEY` for litellm's env-discovery path.
But the call order in `on_message` was:

    conversation, captured = self._get_or_create_conversation(...)
    # ↑ LLM/Agent/Conversation built HERE, env empty → api_key=None

    with agent_secrets.secrets_in_env(_OPENHANDS_SDK_ENV_KEYS):
        await asyncio.to_thread(conversation.send_message, prompt)
        await asyncio.to_thread(conversation.run)

The LLM was built *before* the env window opened, so
`api_key=None` got cached. litellm trusted the explicit None
over env fallback → request went upstream with no
Authorization header → 401.

(`/mcp/rpc` returned 200 in the same trace because OpenHands'
MCP HTTP client reads `mcp_config` headers at *call time*, not
construction time, so the .mcp.json bearer token still flowed.)

Code changes:

- `packages/agent/doorae_agent/integrations/openhands_engine.py:_build_llm`:
  - Reads `OPENAI_API_KEY` and `OPENAI_BASE_URL` directly from
    `agent_secrets` and adds them to the LLM constructor kwargs
    when present. No more env-timing dependency on the critical
    path.
  - Docstring spells out the regression (#366 issue body) and
    explains why `OPENAI_*` is the right scope (the doorae
    gateway is OpenAI-compat regardless of upstream provider —
    every catalog model goes through as `openai/<id>` per
    `openhands_model_id_for_gateway` from #359).
- `secrets_in_env` is preserved around the `run()` call as
  belt-and-suspenders for any litellm path that does read env
  at request time (Anthropic / Gemini direct routes, model-
  specific overrides — none of which the current catalog uses
  but the safety net stays).
- `packages/agent/tests/test_integrations/test_openhands_engine.py`:
  - `TestExplicitApiKey.test_api_key_and_base_url_passed_to_llm_constructor`
    — agent_secrets carries both → LLM receives both as kwargs.
  - `TestExplicitApiKey.test_no_api_key_kwarg_when_secret_absent`
    — agent_secrets empty → LLM constructed without those
    kwargs. Preserves SDK auto-discovery so deployments that
    rely on Bedrock IAM / Vertex ADC / env-passed keys are
    unaffected by #366.

## Decisions

The plan in the commit body weighed three options:

- **Move `_get_or_create_conversation` inside the
  `secrets_in_env` block.** Would work — LLM gets built with
  env populated. But the per-room Conversation cache means
  later messages reuse the cached LLM; if `agent_secrets`
  rotates, the cache remains stale. Also adds an indirect
  control-flow dependency (LLM construction order coupled to
  context-manager scope) that's easy to break in future
  refactors.
- **Pass `secrets={...}` to the `Conversation` constructor.**
  The SDK accepts a `secrets` kwarg per the API reference.
  Cleaner separation between credentials and env, but the
  Conversation API surface is broader and the credential
  passthrough semantics are less documented for our specific
  case (litellm provider routing). Future-friendly but riskier
  for #366.
- **Pass `api_key` / `base_url` explicitly to the LLM
  constructor** (chosen). Direct: the LLM is the thing that
  actually needs the credentials, so wire them straight there.
  No env-timing dependency, no Conversation-side surface
  changes, and the kwarg names map 1:1 to the Pydantic field
  names that are already part of LLM's documented API.

What tipped the scale: this is a focused fix for the specific
401 the user is hitting. Direct constructor kwarg minimizes
diff surface, keeps the per-room Conversation cache semantics
unchanged, and the test (`test_api_key_and_base_url_passed_to_
llm_constructor`) locks the contract by inspecting the kwargs
the constructor receives — exactly the layer that broke.

Explicitly rejected for this commit (deferred):
- Wiring `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` for direct
  provider routes. The current catalog routes everything
  through the gateway as `openai/<id>`, so `OPENAI_*` covers
  every reachable model. When `engine_secrets` later starts
  populating Anthropic/Gemini direct keys (deferred from #359),
  this method gains symmetric branches.
- Refactoring `secrets_in_env` out of the call path entirely.
  The bridge stays around `run()` for any litellm code path
  that still reads env. Removing it could regress a future
  provider integration; the cost of keeping it (already
  shipped in #355) is zero.

Assumptions worth flagging if they break later:
- `agent_secrets.get(key)` returns `None` for missing keys.
  Documented contract; covered in the existing #184 `secrets`
  module.
- LLM constructor accepts `api_key` / `base_url` as kwargs.
  Confirmed via `openhands.sdk.llm.LLM` Pydantic Field
  definitions. If the SDK renames or reshapes those fields, the
  TypeError fallback in `_build_llm` (already present for
  `reasoning_effort`) catches the kwarg rejection and retries
  without — so the worst case is "no api_key, back to env
  fallback", not adapter crash.
- doorae gateway stays OpenAI-compat at `/api/v1/llm/v1`. If
  the gateway grows a separate Anthropic-compat path
  (`/v1/messages`), this method needs ANTHROPIC_API_KEY +
  ANTHROPIC_BASE_URL handling — the engine_secrets schema
  already reserves the keys, just not populated yet.

## Result

`oh-agent04` should respond on the next chat. With #366
deployed, the LLM built per-room receives the gateway-issued
agent token at construction → litellm sends `Authorization:
Bearer agt_...` to the doorae reverse proxy → `get_current_identity`
matches the AgentToken row → request flows to litellm → ollama
→ response back through the chain.

This closes the cascade started at:
- #355 (adapter scaffold)
- #357 (machine detector)
- #359 (gateway engine_secrets wire)
- #362 (litellm cold-start timeout)
- #364 (PATH-shadowed bare litellm binary)
- #366 (LLM constructor api_key plumbing)

Coverage: 365 / 365 agent tests pass (was 363 pre-#366, +2
new); ruff clean on changed files. No cluster / machine
changes; no other regression surface.

Pending follow-ups (separate issues):
- Anthropic / Gemini direct-route engine_secrets when
  operators register those provider keys in the gateway DB
  (the catalog is ready; engine_secrets needs the symmetric
  branches in `gateway_secrets.build_engine_secrets`).
- fastapi 0.124 audit so `litellm[proxy]` could ship inside
  the cluster venv directly (would simplify #364's binary-path
  override to "delete the env var").
- Lifespan-detached gateway spawn so server's port-8001 listen
  isn't blocked while litellm warms up (the vite ECONNREFUSED
  spam during `make dev` boot — cosmetic but noisy).

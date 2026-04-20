# feat(llm-gateway): admin REST API for the embedded gateway (#197 Phase 3)

- Commit: `b27e636` (b27e6365c6f788a498827d98c1d70ade142cd8fa)
- Author: Changyong Um
- Date: 2026-04-20T21:49:15+09:00
- PR: (pending)

## Situation

Phase 2 (#202) stood up the gateway's data layer, supervisor,
reverse proxy, and bootstrap. What was still missing: an HTTP
surface for admins to actually register models, rotate keys,
check on the running process, and apply config changes without
poking the database by hand. The frontend scheduled for Phase 4
needs a concrete contract to wire to; agents depending on the
gateway in Phase 5 need a way for a human operator to turn it on
without ssh'ing into the box.

## Task

Build the `/api/v1/llm-gateway/*` router that:

1. CRUDs `LLMGatewayModel` and `LLMGatewaySecret` with sensible
   409s on duplicate model names.
2. Encrypts secret values at rest with the existing Fernet surface
   and never returns plaintext — list responses carry a
   non-reversible `value_preview` so the UI can show "which key"
   without exposing "what key".
3. Exposes `/status`, `/apply`, and `/restart` against the
   supervisor so admins have visibility into the subprocess and a
   controlled respawn button (the "draft → apply" pattern lives
   entirely in DB state; the admin's edits land immediately, and
   the child process keeps running the previous config until
   `/apply` triggers `supervisor.restart()`).
4. Aggregates `LLMGatewayUsage` rows into `by_model` / `by_agent`
   buckets within a caller-specified window (`24h` default,
   clamped to 30 days).
5. Provides a `/models/{id}/test` ping that exercises the full
   live path — gateway reverse proxy + supervisor's master key +
   litellm + upstream — so admins can validate a new model
   without waiting for an agent to call it.

All endpoints gated by `get_admin_identity`. No new infrastructure
required; reuses the existing MCP secrets `_secrets` accessor.

## Action

**Router** (`packages/cluster/doorae/api/v1/llm_gateway.py`)
- New module. `prefix="/api/v1/llm-gateway"`, `tags=["llm-gateway-admin"]`.
- Pydantic schemas: `ModelCreate`/`ModelUpdate`/`ModelOut`,
  `SecretCreate`/`SecretUpdate`/`SecretOut`,
  `StatusOut`/`TestResult`/`UsageBucket`/`UsageOut`.
  Extra fields rejected (`extra="forbid"`) so a typo can't silently
  drop data.
- `_get_supervisor_or_503` / `_get_upstream_or_503` /
  `_get_gateway_secrets` look up `app.state.llm_gateway_*` and
  return 503 with a specific detail string when absent — keeps the
  "flag off" surface inert without needing to mutate `create_app`.
- `_mask_secret` returns `prefix + '…' + last4`, full-mask for
  strings shorter than 12 chars so a leaked dev token never
  surfaces intact.
- `_parse_window` tolerates `Nh` / `Nd` / raw int / garbage, clamps
  [1h, 30d], defaults to 24h. Admin UI trusts the server default
  instead of bubbling 400s.

**Model endpoints** — `GET/POST/PATCH/DELETE /models` + `POST
/models/{id}/test`. `POST /models` enforces unique `model_name`.
`PATCH` accepts partial updates (`extra_params` can be cleared
with explicit `null`). `/test` picks the upstream path from
`provider` — Anthropic models land on `/v1/messages`, everything
else on `/v1/chat/completions`. Returns `{ok, status_code,
duration_ms, error}`; exceptions surface as `ok=false` rather
than 500 so the UI always has structured data.

**Secret endpoints** — `GET/POST/PATCH/DELETE /secrets`. `POST`
rejects duplicate env_var_names (409 with guidance to use PATCH
to rotate). Storage is `encrypt_dict({"v": plaintext})` via the
shared `MCPSecrets`, matching what Phase 2's
`spawn_params_factory` decrypts with.

**Runtime endpoints** — `GET /status`, `POST /apply`, `POST
/restart`, `GET /usage`. `/status` reads
`supervisor.status()` and maps it to `StatusOut`; `/apply` and
`/restart` both invoke `supervisor.restart()` today but are kept
distinct endpoints so the UI can surface them as separate actions
(config change vs recovery). `/usage` issues two grouped SELECTs
with `func.coalesce(func.sum(...), 0)` so rows with missing token
counts don't blow up the aggregate.

**Router registration** (`doorae/app.py`)
- Imports `llm_gateway_admin_router` alongside the reverse proxy.
- `app.include_router(llm_gateway_admin_router)` right after the
  proxy, inside the same "feature-flag-independent" comment block.
  Handler 503s cover the not-wired case.

**Tests** (`packages/cluster/tests/test_llm_gateway_admin_api.py`)
- Module-level `env` fixture spins up an in-memory SQLite, seeds
  admin + non-admin users, mints JWTs for both, and installs a
  `_FakeSupervisor` (records `restart_count`) plus a stubbed
  `mcp_template_service` so `_get_gateway_secrets` resolves.
- 12 tests cover the contract:
  - Non-admin user hits 403 on a matrix of endpoints; unauth hits
    401/403.
  - Model create → list → patch → delete round-trip; duplicate
    name returns 409.
  - Secret post encrypts (DB ciphertext does not contain "sk-ant-"
    substring), list masks, delete removes.
  - Status snapshot matches supervisor output.
  - `/apply` and `/restart` both increment the fake supervisor's
    `restart_count`.
  - Status 503 when `llm_gateway_supervisor` is set to None.
  - Usage aggregation: three in-window rows + one older row; the
    older one must be excluded from `total_requests`; `by_model`
    sums prompt/completion tokens correctly.
  - `/models/{id}/test` installs a `MockTransport` upstream,
    creates a model, calls /test, and asserts the upstream saw
    `/v1/messages` + `Bearer sk-fake-master`.

## Result

- 12/12 new admin-API tests pass.
- Cluster full regression: **657 passed** (645 Phase 2 + 12 new),
  no existing test changed behaviour.
- `ruff check` clean across the new router + test.
- Contract for Phase 4 frontend fully specified: schemas, status
  codes, and response shapes are all concrete.
- Phase 5 (agent wiring) is unblocked — the admin can now register
  a model + secret and apply it, so there's an end-to-end target
  for the engine_secrets manifest injection.

## What's next

- Phase 4: `AdminLLMGatewayPage` with the secondary-sidebar layout
  documented in §12.4. Uses React Query hooks against the 12
  endpoints above.
- Phase 5: `claude_code.py` / `codex.py` adapters wrapped in
  `secrets_in_env([...])`, plus the manifest builder injecting
  `ANTHROPIC_BASE_URL` / `OPENAI_BASE_URL` / `*_AUTH_TOKEN` when
  the flag is on and the per-agent toggle opts in.

# feat(llm-gateway): backend for embedded LiteLLM proxy (#197 Phase 2)

- Commits: 5 commits from `19929fe` through `61c9f13`
- Author: Changyong Um
- Date: 2026-04-20T21:34:30+09:00
- PR: (pending)

## Situation

Phase 1 of #197 (ADR-004 + design chapter §12) specified the shape
of the embedded LiteLLM gateway, but nothing was wired up yet —
agents still called upstream LLM APIs directly from their machine's
env, with no way to serve machines that had no internet and no way
for operators to see per-room / per-agent token usage.

Phase 2's contract: stand up the backend so that when the feature
flag is on, every agent LLM call enters doorae through
``/api/v1/llm/*``, traverses a locally-bound ``litellm`` subprocess
that the server supervises, and lands a usage row in the database
on the way out.

## Task

Five commits, each an independent PR-shaped step:

1. **Data layer + package skeleton**. Three ORM tables, migration
   026, ``llm_gateway/`` package with public API as stubs, config.py
   additions. No runtime behaviour yet — ships the surface so the
   subsequent steps fit into reviewable chunks.
2. **Supervisor state machine**. Spawn → health → running, crash
   auto-respawn with exponential backoff, Apply-triggered graceful
   restart, teardown. All IO injected at construction so tests
   exercise the state machine with mocks in < 0.1s.
3. **config.yaml renderer**. Pure function over model rows →
   LiteLLM-shaped yaml with every credential emitted as
   ``os.environ/DOORAE_LITELLM_<KEY>`` rather than an inlined value.
4. **Usage parsing + reverse proxy**. ``parse_json_usage`` and
   ``parse_stream_event`` cover Anthropic + OpenAI, SSE + JSON.
   ``/api/v1/llm/{path:path}`` swaps the caller's doorae token for
   the gateway master key, relays the body, records a usage row in
   a background task.
5. **Lifespan bootstrap + Makefile**. Real ``spawn_fn`` using
   ``asyncio.create_subprocess_exec``, real ``health_probe`` using
   a shared ``httpx.AsyncClient``, real ``spawn_params_factory``
   reading DB + decrypting secrets + writing yaml. Makefile targets
   now pull ``litellm[proxy]`` via ``uv tool install`` so the binary
   is on PATH with zero per-invocation overhead.

Non-goals in this phase: admin CRUD API (Phase 3), frontend
(Phase 4), agent-side wiring to actually route via the gateway
(Phase 5). Feature flag stays off by default so none of the above
change runtime behaviour for existing users.

## Action

**Data layer** (`packages/cluster/doorae/db/`)
- `models.py` gained `LLMGatewayModel`, `LLMGatewaySecret`,
  `LLMGatewayUsage`. ``api_key_ref`` is a natural-key reference
  into the secrets table (not a FK) so deleting a secret doesn't
  cascade into model rows. Usage table is indexed on ``timestamp``,
  ``(agent_id, timestamp)``, and ``(model_name, timestamp)`` to
  cover the admin UI's grouping queries cheaply.
- `migrations/versions/026_llm_gateway.py` creates all three
  tables + indexes and drops them in reverse order on downgrade.
  Test `test_migrations.py` updated to assert head is `026`.

**Config** (`packages/cluster/doorae/config.py`)
- `DOORAE_LLM_GATEWAY_ENABLED` (default false) — gates the whole
  feature.
- `DOORAE_LLM_GATEWAY_PORT` (default 4001).
- `DOORAE_LLM_GATEWAY_CONFIG_PATH` (defaults to
  `~/.doorae/litellm.yaml`).

**Supervisor** (`packages/cluster/doorae/llm_gateway/supervisor.py`)
- `GatewayState` enum (INIT / STARTING / RUNNING / CRASHED /
  RESTARTING / STOPPED / FAILED / TERMINATED).
- `LLMGatewaySupervisor` takes `spawn_fn` and `health_probe` at
  construction — no direct subprocess or httpx calls in the class
  itself. Backoff schedule `(1s, 5s, 30s)` is also injectable so
  tests finish in < 0.1s.
- Crash path: watch task awaits `proc.wait()`, recognises whether
  the exit was expected (RESTARTING/STOPPED/TERMINATED states skip
  respawn), bumps `crash_count`, sleeps the scheduled backoff,
  calls `_do_spawn()` again. Backoff exhaustion lands in FAILED so
  an operator sees it in the Status panel rather than an infinite
  loop masking the real fault.
- `restart()` resets the crash counter so a known-good Apply isn't
  throttled by an older bad run's history.
- `_graceful_terminate()` handles proc-already-exited,
  ProcessLookupError, and SIGKILL escalation after the grace
  timeout.

**Config writer** (`packages/cluster/doorae/llm_gateway/config_writer.py`)
- `render_config(models)` filters by `enabled=True`, emits
  `litellm_params` with `model`, `api_key: os.environ/...`, and
  passes through `extra_params` only for keys that don't collide
  with the core fields. `general_settings.master_key` is also an
  env reference; `disable_spend_logs: true` keeps LiteLLM stateless
  (no Postgres dependency per ADR-004).
- `config_hash()` — 16-hex-char sha256 prefix for the Status panel
  to answer "is the running process loading today's DB state?".

**Usage parser** (`packages/cluster/doorae/llm_gateway/usage_logger.py`)
- `parse_json_usage` prefers Anthropic's `input_tokens` /
  `output_tokens` keys; falls back to OpenAI's
  `prompt_tokens` / `completion_tokens`. Missing fields degrade to
  `None` so the caller can still record the request.
- `parse_stream_event` handles Anthropic's `message_start`
  (carries `input_tokens`), `message_delta` (carries
  `output_tokens`), and OpenAI's final chunk-with-usage shape.
  The reverse-proxy keeps the last non-None result.

**Reverse proxy** (`packages/cluster/doorae/llm_gateway/reverse_proxy.py`)
- `APIRouter(prefix="/api/v1/llm", tags=["llm-gateway-proxy"])`
  catches every method at `/{path:path}`.
- Dependencies: existing `get_current_identity` for auth; new
  `get_upstream_client` + `get_supervisor` pull from
  `app.state.llm_gateway_*` and return 503 when the gateway isn't
  wired up. That lets the router be included unconditionally in
  `create_app()` — the handler guards its own preconditions.
- Request path: body + query preserved verbatim; hop-by-hop
  headers stripped; `Authorization` overwritten with
  `Bearer <master_key>`.
- Response path: if upstream responds `text/event-stream`, the body
  is buffered for the MVP (explicit trade-off noted in code — a
  follow-up switches to true chunk relay with streamed usage
  parsing). Otherwise a plain `Response` passes through.
- Usage log: `BackgroundTasks` fires `_write_usage_row` after the
  response is sent so the client's RTT doesn't pay for the DB
  round-trip. Errors are swallowed and logged via structlog.

**Bootstrap** (`packages/cluster/doorae/llm_gateway/bootstrap.py`)
- `_real_spawn` runs `asyncio.create_subprocess_exec("litellm",
  "--config", ..., "--host", "127.0.0.1", "--port", ...)` with
  `env = os.environ.copy() | params.child_env`.
- `_build_health_probe` closes over a shared `httpx.AsyncClient`
  and polls `GET /health` at 0.25s intervals up to ~9s (the
  supervisor caps at 10s total). Connection errors during bind are
  tolerated.
- `_build_spawn_params_factory` is called by the supervisor before
  every spawn (initial start and each Apply/respawn): it reads
  current `LLMGatewayModel` + `LLMGatewaySecret` rows, decrypts
  each secret via `MCPSecrets.decrypt_dict` with the shared
  `DOORAE_MCP_SECRETS_KEY`, renders yaml, writes atomically to the
  configured path with `chmod 600`, and returns `_SpawnParams`
  carrying the master key (constant for the server lifetime so
  Apply doesn't invalidate in-flight requests).
- `bootstrap_gateway(app, config, session_factory, mcp_secrets)`
  assembles everything onto `app.state` and calls `supervisor
  .start()`. `shutdown_gateway(app)` tears it down before engine
  disposal.

**Lifespan** (`packages/cluster/doorae/app.py`)
- Router always included (the 503-on-missing-state pattern above
  makes this safe).
- Startup: after the MCP template service is wired, if
  `config.llm_gateway_enabled` and no pre-wired supervisor, call
  `bootstrap_gateway`. Reaches into
  `mcp_template_service._secrets` so the same Fernet key pair
  works for both systems.
- Shutdown: `shutdown_gateway(app)` runs before the skill cron
  cancel + engine dispose so a respawning litellm can't outlive
  the DB.

**Makefile**
- `install` and `setup` targets now tail-output
  `uv tool install 'litellm[proxy]'`. The `|| true` keeps the
  build green on a machine where the network is down during
  setup; operators can still run the command manually.

**Dependencies** (`packages/cluster/pyproject.toml`)
- `pyyaml>=6.0` pinned directly. It was only reachable
  transitively through `doorae-machine` before, which is too
  fragile now that `llm_gateway/config_writer.py` consumes it as
  a first-class dep.

## Result

- 29 new gateway-specific tests:
  - `test_llm_gateway_supervisor.py` — 7 cases (start happy path,
    health failure → FAILED, single-crash respawn, four-crash
    backoff exhaustion, restart graceful, stop, stop-before-start
    no-op).
  - `test_llm_gateway_config_writer.py` — 9 cases (single-model
    shape, master_key reference, disabled filter, extra_params
    passthrough, empty list validity, plaintext-secret sanity
    check, hash stability, hash sensitivity, hash format).
  - `test_llm_gateway_usage_logger.py` — 8 cases (Anthropic JSON,
    OpenAI JSON, missing usage, partial fields, non-usage event,
    message_delta, message_start, OpenAI final chunk).
  - `test_llm_gateway_reverse_proxy.py` — 5 cases (forwarding,
    master key swap, usage row written, SSE relayed,
    unauthenticated 401).
- Cluster regression: **645 passed** with the feature flag at its
  default `False`. No existing test changed behaviour.
- Ruff clean across the entire gateway package.
- Feature flag stays off. The router 503s until
  `bootstrap_gateway()` wires `app.state.llm_gateway_*`, so
  flipping the flag in a future deploy is the only change needed
  to turn on the new path.
- Phase 3 (admin CRUD API), Phase 4 (admin UI), and Phase 5
  (agent-side manifest wiring + `secrets_in_env` on claude_code /
  codex adapters) remain to be done as separate PRs per the
  plan's rollout schedule.

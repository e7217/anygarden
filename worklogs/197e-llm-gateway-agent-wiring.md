# feat(llm-gateway): agent-side wiring closes the loop (#197 Phase 5)

- Commit: `08855b7` (08855b7ec3b4f6428bf74318551e0fde84a2ded7)
- Author: Changyong Um
- Date: 2026-04-20T22:18:14+09:00
- PR: (pending)

## Situation

Phases 1–4 gave us the shape of the embedded LLM gateway, the
supervised subprocess + reverse proxy behind it (#202), the admin
REST surface (#203), and the admin UI (#205). What was still
missing: the actual **plumbing that routes an agent's LLM call
through doorae-server**. Without Phase 5, flipping the feature
flag would still leave agents calling upstream providers directly,
so the whole gateway investment would be dead weight.

The two remaining edges:

1. The server's spawn manifest needed to tell the agent "use these
   env vars" — `ANTHROPIC_BASE_URL` / `OPENAI_BASE_URL` pointing at
   this doorae-server's `/api/v1/llm/*` path, and an auth token for
   the reverse proxy's identity check.
2. The agent's in-process adapters needed to surface those env
   vars into `os.environ` **just for the SDK call**, not leave them
   in the long-running process's environment where a tool (Bash,
   Read) could read them off `/proc/self/environ`.

## Task

- Populate `engine_secrets` in `_build_sync_frame` when
  `llm_gateway_enabled` is true. Skip engines that don't have a
  known env-var contract (openhands, deepagents, gemini-cli) so
  they keep using host-level credentials until follow-up wiring.
- Figure out how to get the agent's plaintext token into the
  manifest given that the server only keeps the argon2 hash after
  grant. (Answer: machine-side sentinel substitution.)
- Wrap the SDK call sites in `claude_code.py` and `codex.py` with
  `secrets_in_env` so the env vars the SDK reads during credential
  discovery are scoped to the SDK call only.
- Cover each edge with a unit test: server renders the right
  `engine_secrets`, machine expands the sentinel correctly, agent
  surfaces env at the right moment and restores it afterwards.

## Action

**Server** (`packages/cluster/doorae/scheduler/lifecycle.py`,
`doorae/app.py`)

- `AgentLifecycle.__init__` gains an `llm_gateway_enabled: bool`
  keyword argument. `app.py`'s lifespan wires it from
  `config.llm_gateway_enabled`.
- New `_build_gateway_engine_secrets(engine)` returns:
  - `claude-code` → `{"ANTHROPIC_BASE_URL": "<base>/api/v1/llm",
    "ANTHROPIC_AUTH_TOKEN": "@DOORAE_AGENT_TOKEN"}`
  - `codex` → `{"OPENAI_BASE_URL": "<base>/api/v1/llm/v1",
    "OPENAI_API_KEY": "@DOORAE_AGENT_TOKEN"}`
  - anything else → `{}` (falls through to host-level creds).
  - Flag off → `{}`. Empty `server_url` → `{}` (guard against
    emitting a broken base URL).
- `_http_base_url()` converts the existing `_server_url`
  (`ws://...` / `wss://...`) to `http://...` / `https://...`.
  Non-ws URLs pass through unchanged so tests using plain http
  strings also work.
- The frame's `engine_secrets: {}` hardcode in the return dict
  becomes `engine_secrets: engine_secrets` — one line.

**Why the sentinel?** The server can't supply the plaintext agent
token because only the argon2 hash is stored after grant (by
design, matches the `AgentToken` row shape from `#70`-era work).
The machine daemon holds the plaintext in `msg.agent_token` (sent
once via `token_grant`), so it's the right place to substitute.
`@DOORAE_AGENT_TOKEN` is treated as an exact-match sentinel —
values that merely contain the substring are passed through
literally, so a legitimate key that happens to include the magic
text can't be accidentally rewritten. Enforced by the
`test_sentinel_value_must_match_exactly` test.

**Machine** (`packages/machine/doorae_machine/spawner.py`)

- Module-level constant `AGENT_TOKEN_SENTINEL = "@DOORAE_AGENT_TOKEN"`
  kept in lock-step with the server's `AgentLifecycle.AGENT_TOKEN_SENTINEL`
  (cross-referenced in the comment).
- New `_expand_agent_token_sentinel(engine_secrets, agent_token)`:
  returns a fresh dict where every value equal to the sentinel is
  replaced with `agent_token`. Empty input → empty dict. No
  sentinel → fresh copy (tests verify the copy semantics — mutating
  the result doesn't leak into the input).
- `spawn()` calls this right before the JSON encode that feeds
  `proc.stdin`. Existing `engine_secrets via stdin` path (#184) is
  untouched — the expansion sits in front.

**Agent** (`packages/agent/doorae_agent/integrations/claude_code.py`,
`codex.py`)

- `claude_code.py`:
  - Module-level `_ANTHROPIC_SDK_ENV_KEYS = ("ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")`.
  - `_collect_reply` wraps the `async for message in self._query_fn(...)`
    loop with `agent_secrets.secrets_in_env(list(_ANTHROPIC_SDK_ENV_KEYS))`.
    The SDK reads env during its HTTP-client construction (lazy —
    happens on first `__anext__`), so wrapping the iteration loop
    is sufficient.
  - When `engine_secrets` carries none of these keys (operator
    hasn't enabled gateway for this agent) `secrets_in_env` is a
    no-op and the SDK falls back to Bedrock / Vertex / host env
    discovery as before.
- `codex.py`:
  - `_OPENAI_SDK_ENV_KEYS = ("OPENAI_BASE_URL", "OPENAI_API_KEY")`.
  - `start()` wraps the `Codex()` construction line. The SDK not
    only reads env at client build time but also spawns a persistent
    app-server subprocess that inherits env — both paths see the
    gateway values for that one call. After `start()` returns the
    env is clean again.
  - Idempotent: a later restart of the adapter gets a fresh
    `secrets_in_env` scope.

**Tests** — 13 added, no change to existing counts:

- `packages/agent/tests/test_llm_gateway_env_injection.py` (3):
  - ANTHROPIC_* surfaced during the query iteration and gone after.
  - OPENAI_* surfaced during `Codex()` construction.
  - A pre-existing host-level `ANTHROPIC_API_KEY` is restored after
    the SDK call (the context manager saves and restores prior
    values; doesn't scrub).
- `packages/cluster/tests/test_llm_gateway_manifest_injection.py` (6):
  - Flag off returns empty for both engines.
  - `claude-code` and `codex` populate the correct key names.
  - `wss://` in `_server_url` becomes `https://`.
  - Unknown engines (openhands, deepagents, gemini-cli) return empty.
  - Empty `server_url` returns empty even when flag is on.
- `packages/machine/tests/test_gateway_sentinel.py` (4):
  - Sentinel values substituted, literals pass through.
  - Empty input returns empty.
  - No-sentinel input returns a fresh copy.
  - Sentinel must match exactly (prefix/suffix don't count).

## Result

- All three packages pass full regression:
  - agent: **243 passed** (240 + 3 new). One unrelated
    `OPENAI_API_KEY`-dependent test fails — pre-existing, documented
    in the #58 worklog.
  - cluster: **663 passed** (657 + 6 new).
  - machine: **262 passed** (258 + 4 new).
- Ruff clean across the edited files plus the three new tests.
- With Phase 5 merged, flipping `DOORAE_LLM_GATEWAY_ENABLED=true`
  on a doorae-server plus a claude-code or codex agent results in:
  1. The server's next spawn frame carries the gateway env vars.
  2. The machine expands the auth-token sentinel with the agent's
     live doorae token.
  3. The doorae-agent reads the payload on stdin and stores it in
     `agent_secrets`.
  4. When the SDK makes its first call, the adapter's
     `secrets_in_env` block surfaces the values into `os.environ`
     long enough for the SDK's HTTP client to pick them up.
  5. The request lands at `/api/v1/llm/*` with the agent's doorae
     token in the Authorization header.
  6. The reverse proxy swaps the token for the gateway's master
     key and forwards to the local litellm subprocess.
  7. litellm routes the request to the real upstream provider.
- Outside the `secrets_in_env` block the env is clean, so a
  compromised Bash/Read tool call inside the agent can't read the
  gateway token off `/proc/self/environ` between turns.

## What's next

`#197` is structurally complete with Phase 5. The remaining work is
operational/runtime, not design:

- **End-to-end test on a real B-machine scenario**: ssh into a
  box with no outbound internet, point it at a doorae-server that
  has `ANTHROPIC_API_KEY` registered via the admin UI, register a
  claude-code agent there, and confirm it can chat. Nothing in
  Phase 5 prevents this; it just hasn't been exercised with a
  physical network split.
- Per-agent toggle: right now the flag is all-or-nothing for the
  whole server. If an operator wants some agents to use the
  gateway and others to hit upstream directly (e.g. during a
  migration), we'd need an `Agent.use_llm_gateway` column — easy
  follow-up, not blocking.
- Pricing table + real `$` estimates in the Usage tab.
- Per-secret test button now that there's a live end-to-end path
  it can exercise.

All five PRs (#200 docs, #202 backend, #203 admin API, #205 UI,
this one) are stacked against each other and ready to merge in
order.

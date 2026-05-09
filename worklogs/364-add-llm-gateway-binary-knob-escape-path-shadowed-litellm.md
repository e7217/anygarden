# fix(gateway): add llm_gateway_binary config knob to escape PATH-shadowed bare litellm (#364)

- Commit: `703a252`
- Author: Changyong Um
- Date: 2026-05-10
- PR: #364

## Situation

#359 wired `engine_secrets` for openhands and #362 raised the
supervisor's health-probe timeout to 30s, but `oh-agent03` still
returned silence on every chat message. The supervisor's
`/api/v1/llm-gateway/status` showed `state=failed` with
`last_error="health check timeout"` even at 90s. Meanwhile a
direct shell-spawned `litellm --config ~/.doorae/litellm.yaml` got
to a healthy `/health/liveliness` in roughly 5 seconds. The gap
between "direct works in 5s" and "supervisor never reaches 200 in
90s" was the diagnostic the previous fixes never closed.

## Task

Find why supervisor-spawned litellm dies before binding port 4001
and ship a fix that doesn't break the operator's existing
deployment story (the `uv tool install 'litellm[proxy]'` install
they already had at `$HOME/.local/bin/litellm`).

Constraints:
- Pre-#364 deployments where the venv litellm happens to have
  proxy extras must keep working (default behaviour preserved).
- Operator must have a way to point the supervisor at a
  *separate* litellm install when the venv binary lacks
  `[proxy]` extras. Env-var override is the operating UX.
- Tests must lock the kwarg pass-through so a future refactor
  that drops the binary parameter would have caused this exact
  user-reported breakage to resurface.

## Action

Diagnostic step (now reverted — not in the commit):

- Patched `bootstrap.py:_real_spawn` temporarily to redirect the
  supervisor child's stdout/stderr to `~/.doorae/litellm-supervisor.log`.
  The supervisor's litellm landed there with:

      ModuleNotFoundError: No module named 'backoff'
      ImportError: Missing dependency No module named 'backoff'.
                   Run 'pip install litellm[proxy]'

- Confirmed the bare `litellm` package — pulled into
  `.venv/bin/litellm` transitively by `openhands-sdk` after #355
  — was winning the PATH lookup over the operator's
  `~/.local/bin/litellm` (proxy install). `uv run` puts
  `.venv/bin` first in PATH, the bare package crashes on the
  `[proxy]`-only `backoff` import, supervisor never sees a 200.

Permanent fix:

- `packages/cluster/doorae/config.py`: new
  `DooraeSettings.llm_gateway_binary: str = "litellm"`.
  Pydantic loads it from env via
  `DOORAE_LLM_GATEWAY_BINARY`. Docstring spells out the
  `.venv/bin` shadowing trap, the fastapi conflict that blocks
  pinning `litellm[proxy]` directly, and the operator-side
  override path.
- `packages/cluster/doorae/llm_gateway/bootstrap.py:bootstrap_gateway`:
  passes `binary=config.llm_gateway_binary` through to
  `LLMGatewaySupervisor(...)` (the supervisor already had a
  `binary` constructor kwarg from the start; bootstrap was just
  hardcoding `"litellm"` indirectly).
- `docs/decisions/004-embedded-litellm-gateway.md` §1:
  spells out the `<binary>` = `DooraeSettings.llm_gateway_binary`
  contract, the `DOORAE_LLM_GATEWAY_BINARY=$HOME/.local/bin/litellm`
  override pattern, and a #364 regression note explaining why
  pinning `litellm[proxy]` on the cluster package was blocked
  (fastapi conflict).
- `packages/cluster/tests/test_llm_gateway_supervisor.py`:
  `test_binary_passed_to_spawn_fn` locks the contract that the
  supervisor's `binary` kwarg reaches `spawn_fn`'s second
  positional argument. A future refactor that swaps to a
  by-name spawn or drops the kwarg would have caused the
  user-reported breakage to resurface; this test catches it.

## Decisions

The plan started in this branch as "add `litellm[proxy]>=1.83`
to the cluster pyproject" — the obvious-looking fix that would
make `.venv/bin/litellm` carry the proxy extras. Three options
were on the table:

- **Pin `litellm[proxy]` on cluster** (initial attempt). Rejected
  after `uv sync` produced an unsatisfiable resolution:
  `litellm[proxy]==1.83.14 depends on fastapi==0.124.4` while
  `doorae-cluster depends on fastapi>=0.110,<0.120`. Reconciling
  means a fastapi 0.124 compatibility audit across the entire
  cluster surface, which is a separate project.
- **Override PATH inside `_real_spawn`** to put `~/.local/bin`
  first. Rejected because it bakes a host-specific path into
  the production code and breaks any deployment that doesn't
  use `uv tool install`.
- **Add a `llm_gateway_binary` config knob** (chosen). Default
  preserves pre-#364 PATH lookup. Operators with the proxy
  install at a known absolute path get a clean override via
  `DOORAE_LLM_GATEWAY_BINARY=...`. The supervisor already had
  a `binary` parameter from #197 — bootstrap just wasn't
  threading config through.

What tipped the scale: the supervisor's design from #197
already separated "what binary to spawn" from "what config /
port / env it gets" — the binary parameter was sitting unused
through the bootstrap layer. Adding the config knob is one new
field + one kwarg pass-through; rejecting it would mean either
forcing a fastapi audit (large) or hardcoding host paths
(brittle).

Explicitly rejected for this commit (deferred follow-ups):
- fastapi 0.124 upgrade so litellm[proxy] could ship inside the
  cluster venv natively. Tracked separately — the audit needs
  to cover deprecation deltas across all of cluster's FastAPI
  dependents (the auth/dependency, lifespan, route signature
  surfaces).
- Lifespan-detached gateway spawn so server's port-8001 listen
  isn't blocked for ~30s while litellm warms up. The vite
  proxy ECONNREFUSED spam during `make dev` boot is annoying
  but separable from the engine_secrets routing fix this PR
  closes.

Assumptions worth flagging if they break later:
- Operator's litellm[proxy] install path is stable. The current
  recipe uses `uv tool install 'litellm[proxy]'` which lands at
  `$HOME/.local/bin/litellm`. If `uv tool install` ever changes
  its binary location, the runbook + ADR snippet need updating.
- The config-driven binary override is enough for typical
  deployments. If a deployment needs different binaries per
  environment (e.g. dev/staging/prod with version skew), the
  current single-string field can be extended; for now a single
  override keeps the contract small.

## Result

`oh-agent03` chain is unblocked end-to-end. With:

    export DOORAE_LLM_GATEWAY_ENABLED=1
    export DOORAE_LLM_GATEWAY_BINARY="$HOME/.local/bin/litellm"

…and a fresh `make dev` restart, the supervisor's litellm now
spawns from the proxy-enabled install, binds port 4001, and
serves `/health/liveliness 200` within ~5s of boot. The chain
started at #355 (adapter) → #357 (detector) → #359 (gateway
wire) → #362 (timing budget) → #364 (binary path) finally
closes — `oh-agent03` should respond on the next message.

Coverage: 950 / 950 cluster tests pass (was 947 pre-#364, +3
new across this fix); ruff clean on changed files.

Pending follow-ups: fastapi 0.124 compatibility audit (separate
issue), lifespan-detached supervisor spawn so dev boot doesn't
spam vite ECONNREFUSED for 30 seconds (also separate).

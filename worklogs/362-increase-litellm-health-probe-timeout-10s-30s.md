# fix(gateway): increase litellm health-probe timeout (10s → 30s) (#362)

- Commit: `7bb73e9`
- Author: Changyong Um
- Date: 2026-05-10
- PR: #362

## Situation

#359 wired the LLM gateway's `engine_secrets` for openhands so the
adapter could route through the doorae reverse proxy. After
deploying that, an operator who flipped
`DOORAE_LLM_GATEWAY_ENABLED=true` and restarted the server
expected `oh-agent01` to respond — but it stayed silent. Querying
`/api/v1/llm-gateway/status` returned
`{"state":"failed","crash_count":0,"last_error":"health check returned False"}`.
The gateway subprocess was being spawned, then immediately reaped.

## Task

Find why the supervisor's "ok let's check the litellm subprocess
is alive" handshake kept failing on a healthy install, and make
the activation reliable on the operator's hardware without
forcing them to fork the supervisor code.

Constraints:
- Pre-#362 deployments that already had the gateway working (e.g.
  faster boxes) must keep working — don't make the timeout
  require explicit configuration.
- Operators on slower hardware must have an escape hatch when
  even the new default isn't enough.
- The probe loop's termination contract has to be well-defined:
  one source of truth on the deadline, not two layers fighting.

## Action

Diagnosis:
- Direct `litellm --config ~/.doorae/litellm.yaml --host 127.0.0.1
  --port 4002` lands a 200 at `/health/liveliness` after **~12s**
  on a warm dev box. Cold disk would be slower.
- `LLMGatewaySupervisor._HEALTH_TIMEOUT_SEC = 10.0` (the
  `asyncio.wait_for` budget) was on the edge.
- Worse: `bootstrap.py:_build_health_probe` had a hardcoded
  internal `deadline = ... + 9.0`. Even bumping the supervisor's
  outer timeout would have left the inner loop returning False at
  the 9s mark — two layers of timeout, the inner one shorter,
  capping the effective grace period.

Code changes:

- `packages/cluster/doorae/llm_gateway/supervisor.py:73`:
  `_HEALTH_TIMEOUT_SEC` 10.0 → **30.0**. Comment cites the
  observed 12s cold-start figure and points at the new
  `DooraeSettings` knob.
- `packages/cluster/doorae/llm_gateway/bootstrap.py`:
  - `_build_health_probe` lost the inner deadline; the loop is
    now `while True:` and only returns on success. Docstring
    spells out that termination is the supervisor's
    `asyncio.wait_for` (single source of truth).
  - `init_lifespan` constructs the supervisor with
    `health_timeout=config.llm_gateway_health_timeout_sec` so the
    config knob actually flows through.
- `packages/cluster/doorae/config.py`: new
  `llm_gateway_health_timeout_sec: float = 30.0` field on
  `DooraeSettings`. Operators can override via env
  `DOORAE_LLM_GATEWAY_HEALTH_TIMEOUT_SEC`.
- `packages/cluster/tests/test_llm_gateway_bootstrap.py`:
  - `test_health_probe_loops_until_success` simulates 50 connect-
    error retries before a 200 — well past the old 9s window —
    and expects True. Pre-#362 this would have returned False.
  - `test_supervisor_timeout_is_the_authority` wraps the probe
    in `asyncio.wait_for(..., timeout=0.5)` against a
    never-ready client and expects `asyncio.TimeoutError` from
    the outer layer. Locks the probe-docstring contract — a
    future regression that re-adds an inner deadline shorter
    than `wait_for` would cause the outer timeout to never fire,
    which is exactly the shape #362 set out to fix.

## Decisions

The issue body offered a single approach (bump timeout + expose
config + simplify probe). Three sub-decisions inside that:

### Decision 1: 10s → 30s default (vs 15s, 60s)

| Option | Trade-off |
|---|---|
| 15s | Tight margin (12s observed → 3s spare); cold disk regressions would still fail randomly |
| **30s** (chosen) | Comfortably covers observed 12s + plausible doubling on cold disk; first-spawn delay is borne once at boot |
| 60s | Harmless if nothing's wrong, but masks legitimate spawn failures (litellm bug → operator waits a full minute before realising) |

What tipped the scale: the 30s default keeps the operator-visible
"failure to spawn" feedback loop reasonable while leaving margin
for cold-disk spikes. If 30s isn't enough on a particular
deployment the operator can widen via the new config knob — but
the default needs to work for "first-time deploy on dev box".

### Decision 2: Single source of truth for the deadline

Pre-#362 had two layers — supervisor `wait_for` + inner probe
deadline. Both had to agree. The inner deadline was hardcoded at
9s while the supervisor's was 10s, so the inner one always won
and the supervisor's value was effectively dead config. That
shape is brittle: a future change that bumps one without the
other reintroduces the same bug.

Alternative considered: keep both but make the inner one read
from the same `health_timeout` parameter the supervisor uses.
Rejected because it duplicates state — the supervisor already
enforces the timeout via `asyncio.wait_for`, the inner loop
adding its own deadline buys nothing except extra failure modes.

Chosen: remove the inner deadline; supervisor's `wait_for` is
authoritative. Documented in the probe's docstring so a future
maintainer doesn't reintroduce the inner deadline by reflex.

### Decision 3: Expose as config (vs hardcoded constant)

A 30s constant would solve the user's immediate problem but
leaves operators on slower hardware without recourse. Adding the
field to `DooraeSettings` is cheap (Pydantic auto-loads from
`DOORAE_LLM_GATEWAY_HEALTH_TIMEOUT_SEC`) and keeps the workaround
path open without forking the supervisor.

### Assumptions

- litellm 1.83.x cold start ~12s is roughly the upper end on
  warm dev hardware. If a future version regresses past 30s, the
  config knob is the workaround until a release-time fix lands.
- The supervisor's `asyncio.wait_for` correctly cancels the
  probe coroutine on timeout. Standard library guarantees this,
  but if a future supervisor rewrite changes the cancellation
  behaviour, the probe's now-infinite loop would leak — worth
  re-checking when refactoring `_spawn_once`.

## Result

Health timeout chain is fixed end-to-end. With #362 deployed:

- Default 30s covers observed litellm 1.83.x cold start with
  comfortable margin.
- Operators on slower hardware override via
  `DOORAE_LLM_GATEWAY_HEALTH_TIMEOUT_SEC=N` env var.
- The inner probe loop has no termination authority; the
  supervisor's `wait_for` is the single source of truth.

Coverage: 949 / 949 cluster tests pass (was 947 pre-#362, +2
new); ruff clean on changed files.

After redeploy, the operator's `/api/v1/llm-gateway/status`
should return `state=running` within ~12-15 seconds of doorae-
server boot, port 4001 should be listening, and `oh-agent01`
should respond on the next message — closing out the chain
started at #355 (adapter) → #357 (detector) → #359 (gateway
wire) → #362 (health timing).

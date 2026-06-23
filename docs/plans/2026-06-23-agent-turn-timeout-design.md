# Agent Turn Timeout Configuration — Design

**Status**: Design approved 2026-06-23
**Next**: implementation plan via `writing-plans` skill

## Problem

Long agent responses get cut off by the **adapter turn timeout**, the first
timeout that fires when a turn runs long. The current values and override
surfaces are inconsistent across engines, and there is **no way for an operator
to adjust the timeout per agent**:

| Engine | Turn timeout | Override |
|---|---|---|
| gemini | **120s** (`packages/agent/anygarden_agent/integrations/gemini_cli.py:140`) | none — hardcoded module constant |
| codex | **600s** (`integrations/codex.py:108`) | none — hardcoded module constant |
| claude-code | 600s (`integrations/claude_code.py:76-78`) | env `ANYGARDEN_AGENT_CLAUDE_TURN_TIMEOUT_SEC` |
| openhands | 600s (`integrations/openhands_engine.py:74-76`) | env `ANYGARDEN_AGENT_OPENHANDS_TURN_TIMEOUT_SEC` |

When a turn exceeds its adapter timeout the adapter raises `EngineTimeoutError`,
the supervisor maps it to `outcome="timeout"`, and the room is notified with
`"⚠️ 응답이 타임아웃으로 중단되었습니다."`. A slow-but-legitimate agent (a
coding agent doing real work, a reasoning-heavy turn) is therefore truncated
with no recourse, and gemini agents are truncated **4× sooner** than the rest.

Two override gaps make this hard to tune today:
- **codex / gemini** turn timeouts are hardcoded — not adjustable without a code
  change.
- Even the env-adjustable engines can only be tuned **globally via the agent
  process environment**, not per agent, and not from the web UI.

## Goals

1. Let an operator set a **per-agent turn timeout** from the agent settings web
   UI.
2. Provide a **global fallback** so unset agents inherit a sensible default
   (env-controlled).
3. Keep the timeout **layers consistent** — raising the turn timeout must not
   cause a silent drop (WS `ping_timeout`) or premature supervisor kill.
4. **Symmetrize** all four engine adapters so every turn timeout is
   env-overridable (natural follow-up to #483).

## Non-goals

- **Do not** make the cluster orphan-sweep threshold (`1200s`,
  `ANYGARDEN_REQUEST_LIVENESS_SEC`) per-agent. We avoid that complexity by
  **capping** the per-agent value below it (§5). The orphan sweeper stays a
  deployment-global env.
- **Do not** introduce a machine-layer execution deadline. The machine has no
  per-process runtime limit today (`packages/machine/anygarden_machine/supervisor.py:30`
  blocks on `proc.wait()` indefinitely); we are not adding one.
- **Do not** attempt runtime hot-reload of the timeout. Adapter env is read once
  at import time; a changed value takes effect on the **next agent (re)start**,
  same as `reasoning_effort` / `permission_level` / `model` today.

## Design

### 0. Timeout layers (firing order)

The layers a long turn passes through, earliest-firing first:

| Layer | Location | Default | Notes |
|---|---|---|---|
| Adapter turn timeout | `integrations/*.py` | gemini 120s, others 600s | **Primary** — what truncates responses |
| WS `ping_timeout` | `agent/.../client.py:890` | 600s (hardcoded) | If < turn timeout → socket closes mid-turn → **silent drop** (`client.py:874-886`) |
| Supervisor timeout | `runtime/handler_wrapper.py:383` | 900s (env `ANYGARDEN_AGENT_ENGINE_TIMEOUT_SEC`) | Last-resort coroutine cancel → `outcome="timeout"` |
| Cluster orphan sweep | `cluster/scheduler/lifecycle.py:909` | 1200s (env `ANYGARDEN_REQUEST_LIVENESS_SEC`) | Cleans turns with `handler_started` but no `handler_finished` |

**Required invariant** (currently held by hand: 600 ≤ 600 < 900 < 1200):

```
turn_timeout  <  ping_timeout  ≤  supervisor_timeout  <  orphan_threshold
```

### 1. Mental model — single value N + auto-derivation

An operator sets **one number** per agent: "max response time N seconds". The
system derives the other layers from N so the invariant always holds:

```
turn_timeout       = N
ping_timeout       = max(N + PING_SLACK, 600)     # never below current 600
supervisor_timeout = max(N + SUP_SLACK, 900)      # never below current 900
```

with `PING_SLACK = 60`, `SUP_SLACK = 300` (preserves today's 900−600 = 300
relationship). The orphan threshold is **not** derived; instead N is capped so
`supervisor_timeout < orphan_threshold` always holds (§5).

This makes the three agent-side layers move together. The operator cannot
create an inconsistent configuration by adjusting a single dial.

### 2. Resolution chain (each adapter entry point)

```
turn_timeout =  per_agent_N                                 # env ANYGARDEN_AGENT_TURN_TIMEOUT_SEC (spawn-injected)
             ?? ANYGARDEN_AGENT_<ENGINE>_TURN_TIMEOUT_SEC   # global per-engine env
             ?? hardcoded default                           # codex/claude/openhands 600, gemini 120
```

- The per-agent key is **engine-agnostic** (`ANYGARDEN_AGENT_TURN_TIMEOUT_SEC`).
  A gemini agent with a per-agent N uses N; **only when unset** does gemini keep
  its 120s "fast turn profile" default.
- A shared helper `_resolve_turn_timeout(engine) -> (turn, ping, supervisor)`
  centralizes the chain + auto-derivation so all four adapters compute the same
  way.

### 3. PR1 — global symmetrization + auto-derivation (agent package only)

Self-contained, mergeable on its own; delivers global env control immediately.

1. Add env overrides to the two hardcoded adapters:
   - `codex.py:108` → `ANYGARDEN_AGENT_CODEX_TURN_TIMEOUT_SEC` (default 600)
   - `gemini_cli.py:140` → `ANYGARDEN_AGENT_GEMINI_TURN_TIMEOUT_SEC` (default 120)
   - claude/openhands already have theirs → **all four engines env-overridable**.
2. Introduce `_resolve_turn_timeout(engine)` implementing the chain (§2, minus
   the per-agent leg, which PR2 prepends) + auto-derivation (§1). The four
   adapter entry points (`codex.py:616`, `claude_code.py:727`, `gemini_cli.py:586`,
   `openhands_engine.py:1083`) use it to compute the supervisor `engine_timeout`.
3. Replace the hardcoded `ping_timeout=600` (`client.py:890`) with the derived
   value.

**After PR1 alone**: operators can set `ANYGARDEN_AGENT_*_TURN_TIMEOUT_SEC` to
tune any engine globally, and ping/supervisor self-adjust (no silent drop).

### 4. PR2 — per-agent field (DB → API → machine → adapter → UI)

Follows the `permission_level` (#309) end-to-end pattern. Confirmed transport:
the machine spawner uses **explicit per-field mapping** (no generic dict
iteration), exporting `permission_level` as an env var at `spawner.py:923`. The
per-agent timeout rides the same env path.

| # | File | Change |
|---|---|---|
| 1 | `cluster/anygarden/db/models.py` (near L268) | `turn_timeout_sec: Mapped[int \| None]` nullable column, default `None` |
| 2 | `cluster/anygarden/db/migrations/versions/049_*.py` (new, down_revision `"048"`) | `batch_alter_table` + `add_column`, templated on `038_agent_permission_level.py` |
| 3 | `cluster/anygarden/api/v1/agents.py` | `AgentCreate` (L41) field; `AgentUpdate` (L77) `turn_timeout_sec` + `turn_timeout_sec_set` pair; `AgentOut` (L151) field; **range validation** (§5); wire in `update_agent` (L330) under the `runtime_changed=True` branch (forces respawn) |
| 4 | `cluster/anygarden/scheduler/lifecycle.py` (near L875) | payload `"turn_timeout_sec": agent.turn_timeout_sec` |
| 5 | `machine/anygarden_machine/protocol/frames.py` (near L61) | `SyncDesiredStateFrame.turn_timeout_sec: int \| None = None` |
| 6 | `machine/anygarden_machine/daemon.py` (near L531) | `SpawnManifest(turn_timeout_sec=getattr(manifest, "turn_timeout_sec", None))` |
| 7 | `machine/anygarden_machine/spawner.py` | dataclass field (near L79) + `if msg.turn_timeout_sec is not None: env["ANYGARDEN_AGENT_TURN_TIMEOUT_SEC"] = str(msg.turn_timeout_sec)` (near L923) |
| 8 | `cluster/frontend/src/hooks/useAgents.ts` | `interface Agent` (L4-46) + `updateAgent` patch type (L225-270) |
| 9 | `cluster/frontend/src/components/agent-settings/OverviewPanel.tsx` | metadata-grid row "Turn timeout" — numeric input (seconds), empty = global default, inline range hint; handler cloned from `handleReasoningChange` (L247) |

`_resolve_turn_timeout` (PR1) gains the per-agent env as the **first** leg of
the chain — one line.

### 5. Validation & defaults

- **Input form**: free numeric input in seconds (per the UI decision).
- **Range** (validated in the cluster API, which knows the orphan env):
  - lower bound: `N ≥ 30`
  - upper bound: `N + SUP_SLACK < orphan_threshold`
    (with defaults `1200 − 300` ⇒ N must be `< 900s`, i.e. effective max
    **899s / ~15min**; scales up if `ANYGARDEN_REQUEST_LIVENESS_SEC` is raised).
    Reject out-of-range with a clear message.
  - `null` allowed → inherit global fallback.
- **gemini policy**: when per-agent N and the per-engine global env are both
  unset, gemini retains its 120s default. An explicit per-agent N overrides it.
- **Env key naming**:
  - per-agent (engine-agnostic): `ANYGARDEN_AGENT_TURN_TIMEOUT_SEC`
  - global per-engine: `ANYGARDEN_AGENT_<ENGINE>_TURN_TIMEOUT_SEC`
    (`CODEX` / `GEMINI` new; `CLAUDE` / `OPENHANDS` existing)
  - supervisor global: `ANYGARDEN_AGENT_ENGINE_TIMEOUT_SEC` (existing)

### 6. Testing

- **agent** (PR1): unit-test `_resolve_turn_timeout` — chain precedence
  (per-agent > per-engine global > hardcoded default), auto-derivation, and the
  invariant `turn < ping ≤ supervisor` across representative N (small N pinned to
  600/900 floors; large N driving the maxima).
- **cluster** (PR2): `test_agents_api.py` — field CRUD, range validation
  (reject over-cap, accept null), and `turn_timeout_sec` present in the spawn
  payload.
- **frontend** (PR2): `AgentSettingsDialog.test.tsx` — input rendering,
  validation, and patch dispatch.
- **regression**: `uv run pytest packages/` (per-package, per the pytest nuance
  memo), `cd packages/cluster/frontend && npm run build`.

### 7. Rollout

PR1 (agent-only, global env) → merge → operators unblocked globally. PR2
(per-agent + UI) layered on top. Each is independently reviewable and
revertible.

## Resolved decisions (from brainstorming)

- Mental model: **single value N + auto-derivation** (not per-layer dials).
- Value range: **capped below orphan threshold**, agent-side layers auto-derived;
  cluster orphan untouched.
- UI: **free numeric input in seconds**.
- PR split: **two PRs** (global symmetrization, then per-agent).
- Transport: **env** (the `permission_level` pattern), not CLI arg.

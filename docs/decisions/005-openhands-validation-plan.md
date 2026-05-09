# 005 — OpenHands engine validation plan (#355 Phase 5)

> Status: **plan** (results pending — see "Recording results" below)
> Date: 2026-05-09
> Issue: #355

## Context

Phases 0–4 of #355 added the OpenHands V1 SDK as a fourth engine
adapter alongside `claude-code`, `codex`, and `gemini-cli`,
including MCP wiring (#355 Phase 1), skills awareness (Phase 2),
DelegateTool (Phase 3), and a full provider × model catalog
(Phase 4). The remaining migration step before deprecating the CLI
adapters (Phase 6 / future PR) is Phase 5: validate that
OpenHands actually delivers the structural improvements the
migration plan promises — task-transition recognition,
execution-state detection, MCP exposure stability — and that it
doesn't silently regress on dimensions the CLI engines handle
well (latency, multi-turn coherence, abort responsiveness).

This document is the validation **plan**. The actual measurements
require live LLM API calls and operator-driven scenarios that
this PR cannot perform; runtime results land here as an addendum
or in a follow-up PR linked from this file.

## Scenarios

Each scenario runs the same prompt sequence on each of the four
engines (claude-code, codex, gemini-cli, openhands) using a model
that all four can serve (see "Model parity" below). Metrics are
collected from `lifecycle_events` and observable user-side state.

### A — Task transition recognition

**Setup**: agent answers a task, user immediately sends a new
unrelated task before the agent has finished typing.

**What we measure**:
- Does the agent abort or queue the in-flight reply?
- Does the new task receive a clean start (no contamination from
  the prior turn's prompt context)?
- Time from second-message arrival to first token of the new turn.

**Why this matters**: this is the structural pain documented in
the issue body. CLI adapters parse stdout to infer turn
boundaries; if OpenHands' event-sourced state delivers explicit
boundaries, we should see lower variance and zero contamination.

### B — Idle / execution-state detection

**Setup**: agent enters a long-running tool call (e.g. an MCP
filesystem search). Operator polls the agent state every 200ms.

**What we measure**:
- Does the agent report `engine_call_started` reliably?
- When does it report `engine_call_finished`?
- Time from tool completion to user-visible response.

**Why this matters**: CLI adapters infer idle from "no recent
stdout" — a noisy heuristic. OpenHands emits `ActionEvent` /
`ObservationEvent` typed events.

### C — Abort responsiveness

**Setup**: agent enters a long generation. Operator sends an
abort signal (HUP-equivalent through the WebSocket protocol).

**What we measure**:
- Time from abort request to `handler_finished` lifecycle event.
- Does the agent leave the room in a clean state (no orphaned
  typing indicator, no stale Conversation)?

**Why this matters**: SDK in-process abort should be substantially
faster than CLI subprocess SIGTERM.

### D — Streaming stability under long output

**Setup**: ask each agent to produce a 2000-token reply. Capture
inter-token latency distribution.

**What we measure**:
- p50, p95, p99 inter-token latency.
- Number of stalls > 1s.
- Does the typing indicator remain consistent throughout?

**Why this matters**: stdout-buffered streaming in CLI adapters
sometimes batches; SDK streaming should expose tokens at the
provider's actual emission rate.

### E — MCP exposure (regression of #352 → #354)

**Setup**: attach the doorae cluster MCP server to each engine
agent. Ask the LLM to invoke a doorae MCP tool (e.g.
`mark_task_status`).

**What we measure**:
- Does the LLM successfully invoke the tool?
- Does the response reach the agent's reply text?
- Any per-engine adapter error in the lifecycle log?

**Why this matters**: this is the specific regression that PR
#352 tried to fix and PR #354 had to revert. OpenHands consumes
the same `.mcp.json` shape claude-code uses (Phase 1), so it
should pass on day one.

## Model parity

For comparison validity, run each scenario with a model that all
four engines can serve. The natural choice given Phase 4's
catalog parity:

- claude-code:  `claude-sonnet-4-6`
- codex:        `gpt-5.4`
- gemini-cli:   `gemini-3-pro-preview`
- openhands:    rotate `anthropic/claude-sonnet-4-6`,
                `openai/gpt-5.4`, `gemini/gemini-3-pro-preview`

Three openhands runs (one per provider) so we isolate "engine
adapter" from "underlying model" in the comparison.

## Metrics summary table (template)

Fill in as scenarios complete. The "openhands (best)" column shows
the openhands run that performed best across providers, with the
provider noted in parens.

| Scenario | Metric | claude-code | codex | gemini-cli | openhands (best) |
|----------|--------|-------------|-------|------------|------------------|
| A — Task transition | new-turn first-token (ms) | _tbd_ | _tbd_ | _tbd_ | _tbd_ |
| A — Task transition | contamination cases / 20 trials | _tbd_ | _tbd_ | _tbd_ | _tbd_ |
| B — Idle detection | event reliability (% of trials) | _tbd_ | _tbd_ | _tbd_ | _tbd_ |
| B — Idle detection | tool→reply lag (ms) | _tbd_ | _tbd_ | _tbd_ | _tbd_ |
| C — Abort           | abort→finished (ms) | _tbd_ | _tbd_ | _tbd_ | _tbd_ |
| D — Streaming       | inter-token p95 (ms) | _tbd_ | _tbd_ | _tbd_ | _tbd_ |
| D — Streaming       | stalls > 1s / 10 runs | _tbd_ | _tbd_ | _tbd_ | _tbd_ |
| E — MCP             | tool invocation success rate | _tbd_ | _tbd_ | _tbd_ | _tbd_ |

## Decision criteria for Phase 6 (deprecation)

OpenHands is ready to be marked as the recommended engine, with
CLI engines flagged as `deprecated=True` in the catalog, when the
following hold simultaneously:

1. Scenarios A, B, C, E: openhands matches or beats every CLI
   engine on at least one metric per scenario, and is no worse
   than the slowest CLI engine on the others (i.e. the
   "structural" improvements show up empirically).
2. Scenario D: openhands stays within 2× the best CLI engine's
   p95 inter-token latency. (We don't expect raw streaming to be
   faster than a well-tuned subprocess — the win is elsewhere.)
3. No scenario produces an openhands failure rate > 5% across 20
   trials.
4. The SDK's MCP integration (scenario E) holds without per-call
   errors over 50 invocations.

If any of (1)–(4) fail, document the gap in this file and either
fix in a follow-up PR before flipping deprecation, or accept the
tradeoff explicitly.

## Recording results

Append a new section below as runs complete:

    ## Results — <date>, <operator>, <SDK version>

    ### Scenario A
    ...

    ### Scenario B
    ...

The structure stays append-only so a future maintainer can see
the validation trajectory rather than just the final state.

## Out of scope

- Cost / FinOps comparison: each engine has different token cost
  profiles, but the migration's value isn't a cost win — it's a
  structural one. Cost is tracked separately by usage logs.
- Operator UX (model picker, settings UI): tracked under Phase 6
  and #112 / #346 frontend work.
- Sub-agent streaming integration (DelegateTool ↔ doorae channels):
  separate follow-up; the validation here doesn't depend on it.

## Related

- Plan: `.tmp/plan-355-openhands-engine-migration.md`
- Phase 0: `worklogs/355-add-openhands-v1-sdk-adapter-as-4th-engine-phase-0.md`
- Phase 1: `worklogs/355-wire-openhands-mcp-via-shared-mcp-json-phase-1.md`
- Phase 2: `worklogs/355-surface-skills-as-system-prompt-awareness-openhands-phase-2.md`
- Phase 3: `worklogs/355-wire-openhands-delegatetool-into-adapter-phase-3.md`
- Phase 4: `worklogs/355-expand-openhands-catalog-to-full-provider-matrix-phase-4.md`
- Issue: #355
- Earlier MCP-revert pain: #352, #354
- Roadmap context: `docs/plans/2026-04-11-per-agent-directory-skills.md`

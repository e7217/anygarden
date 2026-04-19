# feat(agent): ambient context window for session engines (#74 Stage B)

- Commit: `55e4945` (55e4945 — will become the PR merge sha post-squash)
- Author: Changyong Um
- Date: 2026-04-19
- PR: #74 (second PR in the 2-stage split, follows #139)

## Situation

Stage A (merged as #139, commit `5272cb5`) added a three-way
`MessagePolicy` gate and wired the `[취합 결과]` broadcast to flow
through the new `INGEST_ONLY` path via `metadata.ingest_only=True`.
That solved the specific structural-event case: source-room peers
now see the cross-room synthesis in their Claude Code session
context.

But the gate still silently drops every other non-addressable
ambient message — peer agents replying to the human, humans
addressing someone else in the room, co-participants' chatter. In
a multi-agent collaboration room, those messages are exactly the
signal the window is meant to capture. The plan tracked this as
Stage B, gated on an observation window: enable when the flag-only
path proves insufficient.

The research riff behind it (`docs/research/2026-04-19-multi-agent-
context-injection.md`) calls out that mainstream frameworks
(AutoGen, LangGraph, CAMEL, AgentVerse) default to "shared history
+ speaker selection" — all agents see all messages; we selectively
silence the reply side. Stage A implemented the silence; Stage B
catches up the history side.

## Task

Ship Stage B behind an opt-in environment flag without regressing
Stage A behavior and without touching the raw-SDK adapters that
own their own history:

- Expose a single `DOORAE_CONTEXT_WINDOW_ENABLED` flag that
  promotes rule-5/rule-7 SKIPs to INGEST_ONLY when on.
- Cover `ClaudeCodeAdapter`, `GeminiCliAdapter`, `CodexAdapter` —
  the three session-based engines — with one shared primitive
  rather than three copies of the buffer + TTL + cap + format
  logic that Stage A put into `claude_code.py`.
- Keep all Stage A assertions passing untouched, including the
  ones that import `_PENDING_CONTEXT_MAX` / `_PENDING_CONTEXT_TTL_SEC`
  from `claude_code` and call `adapter._format_context_line(msg)`
  directly.
- Make the env toggle debuggable: log what the accumulator
  actually loaded on agent startup.
- Update README and design doc so operators know the flag exists
  and know what Stage B does differently from Stage A.

## Action

- New module `packages/agent/doorae_agent/coordination/accumulator.py`:
  `ContextAccumulator` owns *policy only* (the `should_capture` rule
  plus the enablement flag). A module-level `get_accumulator()`
  caches the singleton; `reset_for_tests()` lets pytest fixtures
  throw the cache away between env-mutating cases. `_parse_bool`
  treats `"1"/"true"/"yes"/"on"` as truthy and defaults everything
  else off, so a typo in the env value doesn't silently flip the
  feature on.

- New module `packages/agent/doorae_agent/coordination/pending_context.py`:
  factored the per-room buffer primitives out of `claude_code.py` —
  `append_context_line` (prune stale → evict if full → append),
  `drain_context` (pop + TTL sweep), `format_context_line`
  (`[참고] …` breadcrumb renderer). Each adapter instance still
  owns its own `_pending_context: dict` so per-agent isolation
  (Intrinsic Memory Agents arXiv 2508.08997) stays intact.

- `integrations/base.py`: two SKIP sites in `decide_policy` now
  call `_ambient_capture_enabled(msg, client)` before returning
  SKIP. If the accumulator says yes, the message is promoted to
  INGEST_ONLY. Rule 2b (room_query rep-gate), rule 3 (direct
  mention), rule 4 (ingest_only flag), rule 6 (human broadcast)
  all retain their Stage A behavior — Stage B is strictly additive
  in the "this was going to SKIP anyway" lanes.

- `integrations/claude_code.py`: refactored to delegate to the
  shared helpers. `_format_context_line` and `_drain_pending_context`
  are now one-liner back-compat wrappers — Stage A tests exercise
  those method names, so the wrappers keep them source-compatible
  while the real logic moves to `pending_context`. `_PENDING_CONTEXT_MAX`
  / `_PENDING_CONTEXT_TTL_SEC` get re-exported via `__all__` so the
  imports in Stage A test modules don't break.

- `integrations/gemini_cli.py`: added `_pending_context` field,
  `ingest_context` override, and prefix drain in `on_message`. The
  prefix is prepended to the user's content before the conversation
  append, so it lands both in the immediate prompt and in the
  per-room conversation history gemini rebuilds on each call.

- `integrations/codex.py`: same pattern. Codex's per-room `thread`
  owns the history natively — prepending the prefix to
  `thread.run_text(content)` is enough; no explicit session write.

- Both `integrate_with_gemini_cli` and `integrate_with_codex`
  now match on `MessagePolicy` exactly like Stage A's
  `integrate_with_claude_code`.

- `cli.py`: after `ChatClient` construction, log the accumulator
  state (`enabled`, `window_size`). Operators enabling the flag in
  deploy configs can verify via logs without needing to reproduce
  a trigger.

- `tests/test_coordination/test_accumulator.py`: 14 cases covering
  the policy object (disabled baseline, capture filters, env
  parsing truthy/falsy matrix, invalid size fallback, singleton
  caching). `reset_for_tests` is autouse to isolate env mutation.

- `tests/test_integrations/test_should_respond.py`: 6 new
  `TestDecidePolicyStageB` cases pinning the promotion matrix —
  agent ambient captured when enabled, same message skipped when
  disabled, human-mentioning-peer captured, self-message never
  captured, human broadcast still RESPOND, direct mention still
  RESPOND. Every test resets the accumulator fixture.

- `packages/agent/README.md`: new "Context Injection (#74)"
  section documenting the three states, Stage A vs Stage B, the
  opt-in envs, and which adapters are covered.

- Final state: 173 tests pass in `packages/agent/` (150 Stage A +
  23 Stage B). Lint clean on all changed files.

## Decisions

**Singleton vs per-call accumulator.** `get_accumulator()` caches
the instance across the process. A per-call object would re-parse
env every `decide_policy` call (thousands per conversation) and
would let a runtime env mutation silently change policy mid-session.
The plan (`.tmp/plan-74-context-injection-unified.md`, derived
from `plan-74-context-accumulator.md` §3.2 decision 6) treats
Stage B as a deployment-scoped flag; the singleton matches that
assumption. `reset_for_tests` stays as the escape hatch. Rejected
a per-ChatClient instance too — decoupling from the client keeps
`decide_policy` pure-function-ish and removes a parameter from
every adapter that'd need to pass it through.

**Helper module vs three inline copies.** After Stage A, the buffer
logic in `claude_code.py` already looked like template code waiting
to get copy-pasted into Gemini and Codex. Three copies of a TTL
sweep and a FIFO evict was the tipping point — one bug fix would
need three touches. Pulling the primitives into
`coordination/pending_context.py` keeps each adapter's state
ownership (so tests assert directly on `adapter._pending_context`)
but moves the mutation rules to one place. Rejected a full
`PendingContextBuffer` class that also owns the dict — that would
break Stage A test code paths like `adapter._pending_context["r1"]`
indexing, and the resulting migration noise wasn't worth the small
additional cohesion.

**Promote SKIP at rule 5 and rule 7, not earlier.** Stage B does
not override rule 2b (room_query rep-gate), rule 3 (direct mention),
rule 4 (explicit `ingest_only`), rule 6 (human broadcast). Every
one of those carries a decision we trust: rep-gate fires only for
non-representative agents, mention is addressable work, the flag
is a server-side signal, human broadcasts are the "1:1 DM" UX
anchor. Promoting any of them to INGEST_ONLY would lose real
behavior. Rules 5 and 7 are the only ones where the alternative
today is "drop and forget" — those are the ones worth capturing.
Rejected a flat "always try to capture" policy that would have
made the gate harder to reason about.

**Stage B default off.** Stage A ships changes across the fleet
with no env knob. Stage B ships with the knob off. Reason: Stage A
is fixing a concrete reported symptom (`[취합 결과]` missing from
peer context); the cost is bounded (one buffered line per event).
Stage B expands the scope to every ambient message the adapter
sees, which inflates prompt size and token cost in every session-
based adapter simultaneously. The right discovery path is: roll
it out to a single agent, watch prompt size and response quality,
then flip the flag fleet-wide. The env variable surface is the
minimum-friction toggle that supports that rollout without a new
deploy.

**Refactor Stage A code in this PR (vs next PR).** Keeping
Stage A's inline logic and duplicating it into Gemini/Codex would
have produced three copies of the buffer logic — which then have
to be consolidated in PR #3 anyway. Refactoring Stage A into
helpers inside this same PR means one round of regression-test
exercise (173 passes, Stage A assertions included) covers both
the move and the new engines. The back-compat wrappers in
`claude_code.py` exist specifically to keep Stage A's method-name
surface frozen during the refactor.

**Assumptions to revisit**: (i) prompt prefix on Gemini/Codex
behaves like Claude Code's SDK prefix — in-context breadcrumb
interpretation — rather than flowing into the session as if the
user literally said it. Manual smoke on `make dev` with Stage B
enabled is the confirmation step. (ii) env-level enablement is
granular enough — if per-room or per-agent-in-DB toggles become
necessary (e.g. "enable in #standup, not in 1:1s"), the singleton
model will need to move to a per-agent instance. Currently deferred
as explicit out-of-scope in the plan. (iii) the FIFO size cap at
10 is tuned for rooms with ≤10 active participants; a larger
multi-party room might need dynamic sizing. Acceptable risk to
ship on the current default.

## Result

Stage B is merged behind `DOORAE_CONTEXT_WINDOW_ENABLED`. Agents
running with the flag on absorb ambient room traffic as
`[참고] @<sender>: <snippet>` breadcrumbs; the next active turn
sees those as prompt prefix and the engine session keeps them as
context from there.

`uv run pytest packages/agent/tests/` passes 173 tests. The 15
Stage A tests on `ClaudeCodeAdapter.ingest_context` pass without
modification, confirming the helper refactor is functionally
equivalent. The new Stage B coverage pins the promotion matrix
(6 cases) and the accumulator policy (14 cases).

Pending for the rollout: (1) manual smoke on `make dev` to
validate that prompts don't bloat or distract under real agent
workloads, (2) decision on whether to promote Stage B to
default-on after observation, (3) TS agent port tracked in #73,
(4) optional future follow-up to expose per-agent toggles if the
deploy-wide env flag turns out to be too coarse.

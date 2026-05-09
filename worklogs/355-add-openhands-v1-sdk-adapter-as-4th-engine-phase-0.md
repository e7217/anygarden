# feat(agent): add OpenHands V1 SDK adapter as 4th engine (Phase 0) (#355)

- Commit: `85cbcc7` (85cbcc704089c6f13dd31a494f7ab23ef8a8e858)
- Author: Changyong Um
- Date: 2026-05-09T23:15:16+09:00
- PR: #355

## Situation

The three CLI engine adapters (claude-code, codex, gemini-cli) each
spawn a CLI subprocess and parse stdout to infer turn / tool / idle
boundaries. Because every CLI emits different lifecycle signals, doorae
keeps paying an "ist heterogeneity tax" — task-transition detection,
idle/abort handling, and MCP exposure diverge per engine. The most
recent visible cost was PR #352, which exposed doorae MCP tools to
codex/gemini and had to be reverted in #354 because per-engine MCP
manifest formats made the integration unreliable. Centralising context
plumbing in `EngineAdapter.assemble_user_content` (#286) and
`compose_session_context_suffix` (#293) absorbed earlier rounds of the
same tax, but the underlying subprocess + stdout-parsing model was
still the bottleneck.

## Task

Add OpenHands V1 SDK as a fourth, in-process Python engine adapter so
the same routing infrastructure can drive a structured event stream
instead of stdout heuristics — without touching the three CLI engines.
Constraints:

- Coexist with claude-code / codex / gemini-cli; CLI removal is
  explicitly out of scope (tracked separately after Phase 5
  validation).
- Avoid the #292 trap: any new adapter MUST be wired through
  `RoomHandlerSupervisor` (timeout / cycle guard / metrics) and call
  the shared context-plumbing helpers from day one. The four dead
  adapters that #292/#294 removed (openai, anthropic, openhands old,
  deep-agents) all skipped this and silently degraded sessions.
- Multi-provider via litellm-style `<provider>/<model>` strings so a
  single adapter covers Anthropic / OpenAI / Google.
- Keep credentials out of `/proc/self/environ` (#184) — bridge keys
  only for the SDK call window via `secrets_in_env`.

## Action

- `packages/agent/doorae_agent/integrations/openhands_engine.py`
  (new): `OpenHandsAdapter(EngineAdapter)` + `integrate_with_openhands`
  factory. Per-room `Conversation` cache (`_conversations`) so
  multi-turn state survives between messages. `start()` lazy-imports
  `openhands.sdk.{LLM,Agent,Conversation}` and degrades to no-op when
  the package is missing, mirroring the claude-agent-sdk pattern.
  `on_message` runs `assemble_user_content` (#286,
  `<room_conversation>` wrap) → prepends `compose_session_context
  _suffix` (#293, memory + roster) → drives `Conversation.send_message`
  + `Conversation.run` inside an `agent_secrets.secrets_in_env(...)`
  context manager. A capture closure registered as a `Conversation`
  callback dispatches on `type(event).__name__ == "MessageEvent"` and
  pulls assistant text out, defensive against multiple shapes
  (`event.content` as str / list / `event.message.content`).
  `integrate_with_openhands` wires the adapter through
  `RoomHandlerSupervisor` with `engine_name="openhands"` and the same
  `decide_policy` / `/delegate` / `room_query` pre-LLM hooks the other
  adapters use.
- `packages/agent/doorae_agent/integrations/__init__.py`: register
  `"openhands"` in `ENGINES` and `_ADAPTER_CLASSES`.
- `packages/agent/doorae_agent/cli.py`: dispatch branch in
  `_setup_engine` that forwards `name`, `system_prompt`, `model`,
  `reasoning_effort` into `integrate_with_openhands`. The click
  `--engine` choice list is built from `sorted(ENGINES.keys())`, so
  `openhands` shows up automatically.
- `packages/cluster/doorae/engines/catalog.py`: new
  `EngineCatalogEntry(engine="openhands", default_model=
  "anthropic/claude-opus-4-7", ...)` with one model per provider as a
  smoke-test surface (`anthropic/claude-opus-4-7`, `openai/gpt-5.4`,
  `gemini/gemini-3-pro-preview`). Per-model `reasoning_levels`
  narrows to each provider's actual acceptance set so the admin UI
  doesn't show levels that the underlying provider would reject.
- `packages/agent/pyproject.toml`: add `openhands-sdk>=1.21` to
  default dependencies and dev extras. Python ≥3.12 already required,
  matches the SDK floor.
- `packages/agent/docs/engines.md`: rewrite to reflect the current
  three CLI engines plus the new in-process OpenHands adapter,
  including the credential bridging key list and the "engine adding"
  checklist (RoomHandlerSupervisor + context plumbing).
- `packages/agent/tests/test_integrations/test_openhands_engine.py`
  (new, 14 tests): fake `openhands.sdk` module installed via
  `sys.modules`. Covers start lifecycle (with/without SDK),
  `on_message` send+run round-trip, per-room `Conversation` reuse
  (the explicit anti-#292 contract), `<room_conversation>` wrap
  pipeline, memory+roster suffix prepending, `ingest_context`
  buffering, secrets present in env only during the SDK call window,
  `stop` closes every conversation, and `integrate_with_openhands`
  wires the message handler.

## Decisions

The plan in `.tmp/plan-355-openhands-engine-migration.md` weighed four
options before settling on OpenHands V1 SDK:

- **pi-mono (TypeScript)**: ruled out because (a) it can't be
  in-process imported from doorae's Python backend — a TS subprocess
  would re-introduce the same stdout-parsing problem we're trying to
  delete, (b) it deliberately omits sub-agent and plan mode, which
  conflicts with the Phase X delegation roadmap in
  `docs/plans/2026-04-11-per-agent-directory-skills.md`, (c) single
  maintainer with `oh-my-pi` already forking it for the missing
  features.
- **LangChain Deep Agents**: technically equivalent to OpenHands on
  feature surface (sub-agents, MCP, HITL), but pulls in the entire
  LangGraph state-machine stack. doorae already has its own channel /
  room / runtime abstraction, so adopting LangGraph would mean
  running two state machines in parallel. The only piece we actually
  need is something like `SubAgentMiddleware`, and that's not worth
  importing the whole framework for.
- **Direct SDK calls + custom harness**: maximum control but the most
  expensive option — re-implementing event normalisation, tool
  calling, MCP, streaming, abort/steering. Reinventing what
  OpenHands already ships.
- **OpenHands V1 SDK**: chosen. Python in-process import (no
  subprocess), event-sourced `Conversation` with `token_callbacks` /
  callbacks for typed event capture, typed tool system + MCP as
  first-class concerns (Phase 1), `DelegateTool` as the standard
  sub-agent primitive (Phase 3), and litellm-based multi-provider
  routing through a single adapter (Phase 4 expansion).

What tipped the scale was the structural fix for the user's reported
pain: task-transition recognition and execution-state detection are
heuristic in stdout-parsing adapters and structural in event-stream
adapters. pi-mono and OpenHands both deliver this, but OpenHands is
Python (matches the doorae stack), is actively maintained
(MLSys 2026 oral, V0 deprecation in 2026-04 means V1 is now first
class), and its `DelegateTool` model is exactly what the Phase X
roadmap describes. doorae's channel-based sub-agent streaming model
is documented as superior to OpenHands' consolidated-observation
pattern, so the two compose: OpenHands handles delegation lifecycle,
doorae channels carry sub-agent output to the parent.

Migration shape decision — adding a fourth engine instead of
replacing the existing three was driven by the #352 → #354 revert
pattern. Big-bang replacement carries the same regression risk that
caused that revert. Keeping the CLI adapters in place lets us A/B
compare on the same routing infrastructure and only retire them once
Phase 5 validation has signed off.

What I explicitly rejected in Phase 0: registering MCP servers,
exporting agent-dir skills as OpenHands tools, and wiring
`DelegateTool`. Each is a separate Phase (1/2/3) so this PR's blast
radius stays small enough that a regression rolls back cleanly.

Assumptions worth flagging if they break later:
- OpenHands V1 SDK is production-stable post V0 deprecation
  (2026-04-01). Phase 0 is the validation; if PoC reveals
  instability, the adapter interface is generic enough that we can
  swap to direct litellm calls under the same `EngineAdapter` shell.
- The capture closure dispatches on `type(event).__name__ ==
  "MessageEvent"`. If the SDK renames the event class or restructures
  it into a deeper hierarchy, the closure silently captures nothing
  and `on_message` returns `None`. Current tests pin the assumption
  via a fake `MessageEvent` class with the same name; production
  wiring against the real SDK is part of Phase 0 manual verification.
- The litellm `<provider>/<model>` model string format. The catalog
  forwards what it receives directly to `LLM(model=...)`; if litellm
  changes its routing prefix (rare but possible during
  provider-coverage expansion), the catalog entries will need
  adjustment.

## Result

Phase 0 complete: 14 new adapter tests pass; full agent suite stays
green at 335 tests; cluster suite green at 915 tests; machine suite
green at 340 tests; `ruff check` reports no new issues on the
modified files (122 pre-existing errors on `main` are unchanged).
`doorae-agent --help` lists `openhands` alongside the three CLI
engines as a valid `--engine` choice. The catalog validators
correctly narrow `reasoning_effort` per provider — `minimal` is
rejected for Anthropic models and accepted for OpenAI models, as
expected.

Phase 1 (MCP integration), Phase 2 (Skills export), Phase 3
(DelegateTool sub-agents), Phase 4 (multi-provider expansion),
Phase 5 (validation), and Phase 6 (CLI deprecation marking) follow
as separate PRs per the plan. CLI adapter removal remains tracked
outside #355.

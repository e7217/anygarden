# feat(agent): wire OpenHands DelegateTool into adapter (Phase 3) (#355)

- Commit: `e9eeaaa`
- Author: Changyong Um
- Date: 2026-05-09
- PR: #355

## Situation

Phase 0 explicitly stubbed the OpenHands adapter's tools list as
empty, with a comment that Phase 3 would attach `DelegateTool`. The
plan's Phase 3 ties this to the broader Phase X roadmap in
`docs/plans/2026-04-11-per-agent-directory-skills.md`, which notes
that doorae's channel-based sub-agent streaming model is documented
as superior to OpenHands' consolidated-observation pattern, but
attaching the SDK's standard delegation primitive is the
prerequisite for any combined design later. Without it the
OpenHands LLM has no in-thread sub-agent capability, only doorae's
existing cross-room `/delegate` command (which lives in
`integrations/delegate.py` and operates at the channel level, not
inside a single LLM thread).

## Task

Attach OpenHands' built-in `DelegateTool` to every Conversation the
adapter creates, while keeping the same defensive contract earlier
phases established:

- Older SDKs that don't ship the tool, or registry APIs that move
  between revisions, must degrade to "no DelegateTool" — never
  crash the adapter or kill the room.
- Registration is global to the SDK process per the SDK's
  `register_tool(name, cls)` documentation; calling it twice should
  be safe so concurrent adapter instances (tests, multi-agent
  scenarios) don't trip an idempotency error.
- Tool construction must wrap any unexpected exception so a future
  schema change in `Tool(name=...)` lands as a logged warning, not
  a room outage.

Combining DelegateTool with doorae's WebSocket channel model so
sub-agent partial output streams to the parent room is intentionally
out of scope for this commit — that requires runtime SDK exercise
this PR can't safely cover.

## Action

- `packages/agent/doorae_agent/integrations/openhands_engine.py`:
  - `start()` now also imports `Tool` from `openhands.sdk` and
    invokes `_try_register_delegate_tool()` once. The result is
    stashed on `self._delegate_tool_registered` so
    `_get_or_create_conversation` can read it without re-importing.
  - `_try_register_delegate_tool()` (module-level helper) does the
    two imports the SDK's agent-delegation guide describes
    (`openhands.tools.delegate.DelegateTool` +
    `openhands.sdk.tool.register_tool`) and wraps the registry call
    in `try / except`. Either failure logs a structured warning
    (`openhands.delegate_tool_unavailable` /
    `openhands.delegate_tool_register_failed`) and returns `False`.
  - `_tool_cls` instance handle cached on `start` so the per-message
    constructor lookup doesn't repeat the import.
  - `_get_or_create_conversation` builds the agent's `tools` list
    from a small dispatch: when registration succeeded and the Tool
    class is available, append `Tool(name="DelegateTool")`. Tool
    construction wrapped in `try/except` so an unexpected schema
    change degrades gracefully (`openhands.delegate_tool_attach_failed`).
- `packages/agent/tests/test_integrations/test_openhands_engine.py`:
  - The `fake_sdk` fixture grew stubs for `openhands.tools`,
    `openhands.tools.delegate.DelegateTool`, and
    `openhands.sdk.tool.register_tool` (recording into a dict so
    tests can inspect the registry).
  - `TestRegisterDelegateTool` (3 tests) — happy path registers
    the tool; missing module returns `False`; `register_tool`
    raising returns `False`.
  - `TestDelegateToolAttachedToAgent` (2 tests) — happy path
    appends `Tool(name="DelegateTool")` to the agent's tools list;
    failed registration leaves the list empty and the adapter
    still runs.

## Decisions

The plan in `.tmp/plan-355-openhands-engine-migration.md` Phase 3
called for "DelegateTool standard sub-agent + doorae channel
streaming combined". Three approaches were on the table for *this
commit*:

- **Just attach DelegateTool, OpenHands' standard primitive**
  (chosen). Smallest diff, no runtime SDK exercise required,
  unblocks the in-thread sub-agent capability the LLM gains
  immediately. Doesn't bridge to doorae channels yet.
- **Build a doorae-specific delegate tool** that integrates with
  the existing `integrations/delegate.py` channel-based command.
  Rejected because it conflates two layers — the LLM-thread
  delegation OpenHands already ships and the cross-room
  delegation doorae has — and forces the LLM to learn a custom
  doorae tool when the standard `DelegateTool` exists.
- **Skip Phase 3 entirely until runtime SDK validation is
  available**. Rejected because the registration / attach path is
  small and the defensive contract (older SDKs degrade gracefully)
  is exactly the kind of thing static + mock-test coverage
  protects well. The runtime-heavy part (channel streaming
  combination) is the deferred follow-up, not the registration
  itself.

What tipped the scale: Phase 3 has two halves. The "register the
SDK's built-in tool so the LLM has the capability" half is small,
testable in mocks, and unblocks Phase 5 validation scenarios that
involve sub-agent delegation. The "stream sub-agent partial output
through doorae channels" half is design-heavy and runtime-dependent.
Splitting them — landing the registration here, deferring the
streaming combination — keeps the PR's blast radius small while
still moving the migration plan forward.

Explicitly rejected for this commit (deferred):
- doorae channel ↔ sub-agent partial-output streaming. The plan
  documents this as the place doorae has an architectural
  advantage; combining the two is its own design exercise.
- Wiring `DelegateTool` to actually spawn doorae sub-agent rooms
  rather than OpenHands sub-agent threads. Different scope; would
  need a custom DelegateTool subclass or a wrapper.
- Tool filtering via `filter_tools_regex` (covered in Phase 1's
  deferred list).

Assumptions worth flagging if they break later:
- `register_tool(name, cls)` is the SDK's stable registration API.
  Documented in the agent-delegation guide. If the SDK switches to
  a class-based registry or a constructor parameter, the wrapper
  will need updating but the defensive try/except keeps the
  failure mode safe.
- `Tool(name="DelegateTool")` is the LLM-facing reference shape.
  If the SDK starts requiring richer args (per-conversation
  config, capability enumeration), the construction site will need
  more parameters; the try/except around it currently treats any
  TypeError as "skip the tool" and the adapter still boots.
- `register_tool` is idempotent. The SDK's docs describe the call
  populating a name-keyed registry, so repeated calls upsert. If
  a future revision raises on duplicate registration, the
  try/except in `_try_register_delegate_tool` already handles it
  by logging and returning False — the adapter would still boot
  but lose DelegateTool until the SDK behaviour is reconciled.

## Result

Phase 3 complete. Every Conversation the adapter creates now ships
with `Tool(name="DelegateTool")` in its tools list when the SDK
supports it; older SDKs or builds without `openhands.tools.delegate`
get an empty tools list and the adapter boots normally. The LLM
gains in-thread spawn / delegate capability per the SDK's
agent-delegation guide.

Coverage: 42 / 42 OpenHands adapter tests pass (5 new for Phase 3
on top of the cumulative 37 from earlier phases). The `fake_sdk`
fixture's expansion (Tool stub, register_tool registry, delegate
stub) covers the happy path and both degradation paths.

Still pending: doorae channel + DelegateTool streaming combination
(separate follow-up requiring runtime SDK validation), Phase 4
(multi-provider catalog expansion + LLM gateway integration),
Phase 5 (validation scenarios), Phase 6 (CLI engine deprecation
marking).

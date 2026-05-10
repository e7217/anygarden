# fix(agent/openhands): capture assistant text from MessageEvent.llm_message (#372)

- Commit: `ae6430b`
- Author: Changyong Um
- Date: 2026-05-10
- PR: #372

## Situation

After #369 cached the per-agent token so frame rebuilds stopped
orphaning rows, the gateway-auth chain finally produced 200 OK on
the LLM call:

    POST /api/v1/llm/v1/chat/completions HTTP/1.1 200 OK
    handler_finished outcome=ok duration_ms=6571

But the user-visible reply still didn't arrive. typing→false
fired, the lifecycle event reported "ok", and the room stayed
silent. The adapter's `on_message` was returning None despite the
LLM responding successfully.

## Task

Find why the SDK's `MessageEvent` callbacks never produced text in
the captured list and fix the schema mismatch. Constraints:

- The fix must reflect the *actual* SDK schema, not assumptions
  carried forward from #355's Phase 0 stubs.
- Tool-execution dumps (MessageEvent with `source='agent'` but
  `llm_message.role='tool'`) must NOT leak into the user reply.
- Multi-part assistant turns (LLMs that emit multiple
  `TextContent` blocks per message) must concatenate cleanly.
- Tests must lock the regression — fixture's `MessageEvent` shape
  has to match the real SDK so a future drift breaks loudly.

## Action

Diagnostic: grep'd `openhands.sdk.event.llm_convertible.message`
and confirmed the schema:

    class MessageEvent(LLMConvertibleEvent):
        source: SourceType        # 'agent' / 'user' (NOT 'role')
        llm_message: Message       # NOT 'message' / 'content'

    class Message(BaseModel):
        role: Literal['user', 'assistant', 'system', 'tool']
        content: Sequence[TextContent | ImageContent]

Pre-#372 `_capture_assistant` checked `event.role` /
`event.content` (both nonexistent), so every event silently
failed the gate and `captured` stayed empty.

Code changes:

- `packages/agent/doorae_agent/integrations/openhands_engine.py:_capture_assistant`:
  - Reads `event.source` with `.value` fallback for StrEnum
    shapes (defensive against the SDK ever switching from a
    plain string to an enum class).
  - Gates on `source == 'agent'`.
  - Reads `event.llm_message`; gates on `llm_message.role ==
    'assistant'` so tool-execution dumps stay out of the reply.
  - Iterates `llm_message.content` collecting `.text` from each
    part. Multi-part assistant turns concatenate in order.
  - Docstring spells out #372 explicitly so a future contributor
    doesn't restore the old field names.
- `packages/agent/tests/test_integrations/test_openhands_engine.py`:
  - Fixture's `MessageEvent` rebuilt to mirror the real SDK:
    `source: str` + `llm_message: _Message` (with `role` +
    `content: list[TextContent]`).
  - `TextContent` and `_Message` stubs added at fixture level.
  - `_make_assistant_event(text)` helper exposed via the
    `fake_sdk` state dict (`make_assistant_event` /
    `MessageEvent` / `TextContent`).
  - 4 new `TestCaptureFromLLMMessage` tests:
    - `test_assistant_message_captured` — happy path, the fake
      Conversation.run synthesizes an assistant MessageEvent and
      `on_message` returns the captured text.
    - `test_user_source_event_skipped` — `source='user'` →
      capture stays empty → `on_message` returns None.
    - `test_tool_role_message_skipped` — `source='agent'` but
      `llm_message.role='tool'` → skipped.
    - `test_multiple_text_parts_concatenated` — assistant
      message with two `TextContent` parts → reply is
      concatenation in order.

## Decisions

The plan in the issue body weighed three approaches:

- **Read fields the actual SDK exposes** (chosen). Direct
  schema correspondence. Fixture stub rebuilt to match so
  future schema drift fails the test instead of silently
  failing in production.
- **Add `isinstance(event, openhands.sdk.event.MessageEvent)`
  check.** Cleaner than `type(event).__name__` string
  comparison but pulls a hard import dependency. The whole
  fixture-based test pattern relies on `__name__` matching;
  switching now would mean every test re-importing the real
  SDK.
- **Subscribe to the `Conversation.state.events` list after
  `run()` returns** instead of using callbacks. The SDK
  exposes a state object that retains all events. More
  resilient to callback-timing edge cases. Rejected because
  it changes the architecture of the adapter (sync state
  inspection vs async callback pattern) and the callback
  approach already had #355's defensive shape — it just needed
  the right fields.

What tipped the scale: the fix is small and focused, and the
fixture rebuild is what would have caught this issue at #355
review time if the fixture had matched the real SDK from the
start. Aligning fixture with reality is the durable
investment.

What I rejected:

- Restoring fallback to `event.message.content` and
  `event.content` for "future SDK reshape resilience". That's
  exactly the pattern that caused #372 — defensive fallbacks
  hid the schema mismatch. Cleaner to fail loudly when the
  schema changes than to silently capture nothing.
- Implementing a separate code path for ImageContent. The
  current `getattr(part, "text", "")` correctly skips parts
  without text; adding image handling is a separate scope
  (image responses in chat → fronetnd renders; not yet wired).

Assumptions worth flagging if they break later:

- `MessageEvent.source` is the field for agent-vs-user
  discrimination. Documented in the SDK source; if a future
  rev moves to a `direction` or similar field, the gate breaks
  silently (back to "no replies arrive"). The fixture's
  `source='agent'` literal would also start failing the new
  gate, which is the loud failure mode we want.
- `Message.role == 'assistant'` cleanly separates user-facing
  reply text from tool-execution dumps. If the SDK ever
  starts emitting tool results as `role='assistant'` (would
  be unusual), the tool-output skip stops working. Manual
  validation against the live SDK during Phase 5 (#355) would
  catch this.
- `TextContent.text` is the field name. Pre-#372 the fallback
  also iterated `getattr(part, "text", ...)` so this part has
  always been correct.

## Result

The 7-step diagnosis chain finally closes:

| # | Symptom | Root cause | Fix |
|---|---|---|---|
| #355 | OpenHands engine added | new feature | adapter |
| #357 | UI doesn't list openhands | machine detector binary-only | Python import detection |
| #359 | engine_secrets always empty | Phase 5 of #197 unimplemented | helper + plumbing |
| #362 | gateway state=failed (timeout) | litellm cold start > 10s | timeout config |
| #364 | gateway dies on import error | bare litellm in venv shadows user-tool | binary path config |
| #366 | gateway 401 with no Bearer | LLM constructor cached api_key=None | explicit kwargs |
| #369 | gateway 401 with bogus Bearer | frame rebuilds orphan agent_tokens rows | per-agent token cache |
| **#372** | **gateway 200 but no reply** | **MessageEvent schema mismatch (`event.role` vs `event.source`/`llm_message`)** | **read correct SDK fields** |

Coverage: 369 / 369 agent tests pass (was 365 pre-#372, +4
new); ruff clean.

Lesson worth recording for future SDK integrations: the
fake-SDK pattern is fast for tests but doesn't catch
fixture-vs-reality schema drift. Either pin fixture to the
real types (lighter import vs schema fidelity tradeoff) or add
a smoke-test against the real SDK at CI time. Preferably the
latter — even a smoke test that just imports the real SDK and
confirms field names against fixture would have caught this
during #355's review.

After redeploying, oh-agent04 should respond with actual text.
The 8-PR cascade (#355–#372) finally produces user-visible
output.

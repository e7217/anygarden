# feat(agent/openhands): register Terminal/FileEditor/TaskTracker runtime tools

- Commit: `622cd49` (622cd498778eb246a97034241db0e6d1401aaf0b)
- Author: Changyong Um
- Date: 2026-05-10T15:42:25+09:00
- PR: —

## Situation

The OpenHands V1 SDK adapter (#355) shipped with only the SDK's
auto-registered builtins (``FinishTool`` + ``ThinkTool``) plus a
best-effort ``DelegateTool`` registration. No shell, file-editing, or
task-tracking tools were attached. Live observation against
``oh-agent04`` (running ``openai/qwen3.6:27b`` through the local
LiteLLM gateway) showed any prompt that required real work
("호스트 이름이 뭐야?", "호스트 메모리 상태?") returning only a
short preamble ("호스트 이름을 확인하겠습니다.") with no actual
tool execution. ``engine_call_finished outcome=ok duration_ms≈18000``
yet the user-visible answer never materialised.

The trap is in OpenHands' response dispatcher
(``openhands/sdk/agent/response_dispatch.py:225-237``): when the LLM
returns plain text content with no ``tool_calls``, the SDK takes the
``_handle_content_response`` path which sets
``execution_status = FINISHED`` and the run loop exits after that one
message. Smaller models that "narrate" intent without producing a
real tool call therefore terminate after the preamble.

## Task

- Give the agent enough tools that the model has a real path to
  satisfy operational queries (shell, file edit, task list).
- Match the existing best-effort ``_try_register_delegate_tool``
  pattern: each registration must degrade gracefully so a partial
  ``openhands-tools`` install still contributes whatever it can.
- Lock the contract with regression tests that mirror the existing
  fake-SDK fixture (no real ``openhands-sdk`` import in tests).
- Skip browser tools (heavy dependency, not needed for chat) and
  the sub-agent ``TaskToolSet`` (overlaps ``DelegateTool``).

## Action

- Added ``openhands-tools>=1.21`` to ``packages/agent/pyproject.toml``
  under both ``dependencies`` and ``dev`` extras.
- Added ``importlib`` import and a new helper
  ``_try_register_runtime_tools()`` in
  ``packages/agent/doorae_agent/integrations/openhands_engine.py:630-720``.
  ``_RUNTIME_TOOL_SPECS`` lists the three tools to register
  (``TerminalTool``, ``FileEditorTool``, ``TaskTrackerTool``); each is
  imported via ``importlib.import_module`` and registered through
  ``openhands.sdk.tool.register_tool``. Per-tool failures are logged
  via ``structlog`` and the loop continues, so the helper returns the
  list of names that registered.
- Wired the helper into the adapter:
  - ``OpenHandsAdapter.__init__`` gained
    ``self._runtime_tool_names: list[str] = []``.
  - ``start()`` calls ``_try_register_runtime_tools()`` after the
    delegate registration and includes ``runtime_tools=...`` in the
    ``openhands.initialized`` log entry.
  - ``_get_or_create_conversation`` appends a
    ``Tool(name=tool_name)`` for every registered runtime tool name,
    after the existing ``DelegateTool`` attach.
- Extended ``packages/agent/tests/test_integrations/test_openhands_engine.py``
  with stubs for ``openhands.tools.terminal``,
  ``openhands.tools.file_editor``, ``openhands.tools.task_tracker``
  in the ``fake_sdk`` fixture, and added two test classes:
  - ``TestRegisterRuntimeTools`` (4 cases): full registration, per-
    tool skip when a module is missing, per-tool skip on
    ``register_tool`` exception, empty list when the
    ``openhands.sdk.tool`` import fails.
  - ``TestRuntimeToolsAttachedToAgent`` (3 cases): all four tools
    appear in agent ``tools`` (DelegateTool + 3 runtime), partial
    presence when one tool module is missing, DelegateTool-only
    when the entire tools package is unavailable.
- Updated the existing ``TestDelegateToolAttachedToAgent`` to assert
  membership by name rather than ``len(tools) == 1`` so it
  coexists with the new runtime tools.

## Decisions

- **Match the DelegateTool pattern instead of using
  ``openhands.tools.preset.default.get_default_tools()``**: the preset
  is convenient but bundles browser tools by default and pulls in a
  condenser. The existing adapter already manages tools via the
  best-effort ``_try_register_delegate_tool`` style, so a parallel
  ``_try_register_runtime_tools`` keeps the architecture symmetric and
  surfaces per-tool failures cleanly. Browser/condenser can be added
  later as separate deliberate calls.
- **Register under class-name keys (e.g. ``register_tool("TerminalTool", TerminalTool)``)
  rather than the canonical short names (``"terminal"`` etc.)**: this
  mirrors the existing DelegateTool registration. The
  ``openhands.tools.*`` modules auto-register under the canonical
  short names on import, so our explicit calls add a *duplicate*
  registry entry under the class name. ``register_tool`` logs a
  duplicate-name warning but the entry resolves correctly when the
  agent looks up ``Tool(name="TerminalTool")``. Verified live against
  ``openhands-sdk==1.21.1`` — both keys point at the same factory.
- **Skip ``BrowserToolSet``** — heavy ``browser-use`` dependency, no
  current chat-agent need, can be added later under a feature flag
  if a use case appears.
- **Skip ``TaskToolSet``** — overlaps with ``DelegateTool`` (sub-agent
  delegation) and would create two paths for the same capability.
- **Trigger to revisit**: if a future ``openhands-tools`` release
  changes the auto-registration behaviour or removes one of the
  three tool modules, the per-tool gate will silently drop that tool
  from the agent's catalog. The new
  ``test_skips_individually_when_module_missing`` lock should fail
  loudly in that case.

## Result

- ``oh-agent04`` (post-restart) executed ``free -h`` end-to-end on
  the next "호스트 메모리 상태?" prompt: ``engine_call_finished``
  duration jumped from ~5–18s (preamble only) to ~43s (preamble +
  tool exec + summary), and the response carried real values
  (``8.6Gi``, ``7.4Gi``, swap state).
- ``packages/agent`` test suite: 380 passed (previously 373 — 7 new
  cases added).
- ``ruff check`` clean across the touched files.
- A separate model-behaviour issue (qwen wraps its summary in a
  Gemini-style ``{"tool_code", "tool_output"}`` envelope on the
  second turn) surfaced once tools actually executed; that is the
  scope of a sibling fix on the gateway side and not addressed here.

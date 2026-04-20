# fix(agent,cluster): deliver codex responses that span long tool turns (#190)

- Commit: `701d862` (701d862ad37caccbaef9df729cb2951e29299109)
- Author: Changyong Um
- Date: 2026-04-20T09:02:42+09:00
- PR: #190

## Situation

Users reported that agent01-codex's "타자 치는 중" indicator would appear after a cross-room `[ROOM_QUERY]` forward and then disappear without any message actually landing in the target room, while agent01-gemini in the same room responded normally. Short @-mentioned PONG tests succeeded, so the agent wasn't simply dead; long tool-using turns (web-search for GitHub issues, etc.) were the failure case. The codex session rollout file on disk showed a full `task_complete` with the final assistant text, so the model had actually produced an answer — but the chat room never received it.

## Task

Deliver the answer that codex already computed, without letting a single slow turn freeze the room's WebSocket receive loop. Constraints:

- No change to the task boundaries: PONG-level turns still have to land in <5 seconds.
- The adapter cannot rely on the codex-python SDK being bug-free; it ships a vendored Rust binary that can drift ahead of the Python protocol types.
- The fix has to cover the full chain — adapter, client WebSocket, uvicorn WebSocket — because any one of them closing on a 5-minute turn loses the message.
- Existing unit tests in `test_codex.py` / `test_client.py` must keep passing, and the new behaviour needs regression coverage.

## Action

Live instrumentation (`/tmp/doorae-codex-debug.log`) caught three stacked failures across multiple reproductions of the failing ROOM_QUERY. Each got its own targeted fix:

**1. SDK protocol tolerance** (`packages/agent/doorae_agent/integrations/codex.py:46-151`)

- Added `_make_lenient_parse_notification(original, generic_notification_cls, error_cls)` — a top-level factory that wraps `parse_notification` to always call the original with `strict=False` and, when the SDK still raises `AppServerProtocolError` on a known-method / unknown-payload combination (`item/completed` with new `ThreadItem` variants from the bundled codex-cli 0.114.0 binary), downgrades the frame to a `GenericNotification(method=..., params=dict(...))`. Truly malformed frames (non-string method, non-Mapping params) re-raise.
- Added `_install_parse_notification_shim()` — idempotent (guarded by `_PARSE_NOTIFICATION_PATCHED`), defensive (`hasattr` guards + warning-only import failure path), and — crucially — patches both `codex.app_server._protocol_helpers.parse_notification` **and** `codex.app_server._session.parse_notification`. The second patch is what actually affects runtime behaviour because `_session.py` does `from codex.app_server._protocol_helpers import parse_notification` at import time, which binds a local name pointing at the *original* function. The shim is installed once, from `CodexAdapter.start()` right after the codex SDK is successfully imported.

**2. Turn timeout + clean abort** (`codex.py:257-283`)

- Introduced `_CODEX_TURN_TIMEOUT = 600` and wrapped `thread.run_text` in `asyncio.wait_for(asyncio.to_thread(run_text, content, signal=threading.Event()), timeout=600)`. On `asyncio.TimeoutError`, the signal is set (codex SDK's `_SignalWatcher` polls it and interrupts the stream) and the thread is popped from `self._threads` so the next message starts fresh. Exception path also logs `codex.turn_failed` and evicts the thread, matching the pre-existing behaviour.

**3. Keepalive, both sides**

- `packages/agent/doorae_agent/client.py:516-534` — the agent's `ws_connect(ws_url, subprotocols=...)` now passes `ping_interval=60, ping_timeout=600`. Default `20/20` was aborting the client's own keepalive monitor during 5-minute turns, which closed the connection and surfaced as `ConnectionClosedError: sent 1011 (internal error) keepalive ping timeout`.
- `packages/cluster/doorae/cli.py:125-141` — `uvicorn.run(..., ws_ping_interval=60, ws_ping_timeout=600)` so the server side doesn't preemptively close the socket while the agent is mid-turn. Without this, the server would drop the connection TCP-style (`no close frame received or sent`) after ~5 min even though the client's keepalive was relaxed.
- `packages/cluster/Makefile:16` — the `server-dev` recipe gained `--ws-ping-interval 60 --ws-ping-timeout 600` so `make dev` stays in sync with the production code path.

**4. Stale comment** (`packages/agent/doorae_agent/integrations/gemini_cli.py:58-62`)

- Updated the "Matches the codex adapter (120s)" block to point at codex's new 600s budget and explain why gemini deliberately stays at 120s (shorter turn profile).

**5. Regression coverage**

- `tests/test_integrations/test_codex.py:` added `TestInstallShim` (two tests — patches both modules, idempotent) and `TestLenientParseNotification` (four tests — passthrough on success, fallback on protocol error, re-raise on non-string method, re-raise on non-Mapping params), plus `TestCodexTurnTimeout` (timeout path aborts + evicts thread, happy path passes signal). The existing `test_on_message_creates_thread_and_returns_response` was tightened to assert the `signal=` kwarg is always a `threading.Event`.
- `tests/test_client.py:` added `TestWebSocketKeepalive.test_room_loop_passes_extended_keepalive_kwargs`, which patches `ws_connect` and inspects the kwargs to pin `ping_interval=60, ping_timeout=600`.

All 221 agent tests pass (one unrelated `test_openai.py` case is skipped because it requires `OPENAI_API_KEY` in the local env).

## Decisions

Three non-obvious choices were made, all captured in `.tmp/plan-190-codex-protocol-error-recovery.md §3.2`:

- **Primary = monkey-patch `parse_notification`, not opt-out.** Rejected `opt_out_notification_methods=("item/completed",)` because `item/completed` is the main feed for final_text aggregation; silencing it would risk losing the answer even when the turn completes. Rejected upgrading codex-python (1.114.2 is already PyPI latest and the release is the bug). The app-server keeps writing the full task to its session file whether the Python SDK crashes mid-stream or not, so all we need is to let the stream keep flowing past the one unrecognisable payload. Decisive evidence: on the first successful run the session file wrote `task_complete` with the full 554-char answer at 15:28:08 on a run where the Python side had crashed at ~54s — proving the stream is resumable if we don't die on the bad frame.
- **Patch both SDK submodules.** Monkey-patching only `_protocol_helpers.parse_notification` was the obvious choice and it did *not* work; the second live test produced the exact same `AppServerProtocolError` traceback. Grepping the SDK (`.venv/.../codex/app_server/_session.py:16`) revealed the from-import binding that the hot path actually calls. This is a common Python monkey-patching pitfall and the regression test pins it so a future refactor can't silently revert it.
- **Keepalive extended on both client and server.** After the first live validation with only the client-side extension the send still failed, with a subtly different close reason (`no close frame received or sent` vs the original `sent 1011 keepalive ping timeout`). The new error signature plus the 304s elapsed time narrowed this to the uvicorn default `ws_ping_timeout=20`. 600s was chosen to exactly match `_CODEX_TURN_TIMEOUT` — the agent's own turn budget becomes the upper bound on how long the connection ever needs to survive without an app-level message.

Assumption that should trigger a revisit: if codex-python publishes a 1.115+ release whose bundled binary no longer emits the variant payloads, the `_make_lenient_parse_notification` shim becomes cosmetic and the idempotent install path can be retired. The `codex.unknown_notification_tolerated` DEBUG log lets us watch the shim's hit rate in production as that release rolls out.

## Result

Verified end-to-end against the exact ROOM_QUERY that had been silently dropping for the user:

- codex ran the full tool chain for 390.13s (6:30), returned a 1337-character response.
- `handle.send_ok seq=118` fired — the send finally succeeded.
- `seq=119` from `agent01-codex` appeared in 테스트룸2 alongside agent01-gemini's reply.

`codex.parse_notification_shim_installed` is now logged at agent startup, and `codex.unknown_notification_tolerated` fires at DEBUG whenever the bundled binary sends a payload the Python SDK doesn't recognise. Adding coverage for the two most reversible regressions — accidentally dropping the `_session` patch, and the keepalive kwargs drifting back to default — is built into the unit tests.

Still pending (out of scope for this PR): the `[취합 결과]` collection in `room_query._register_multi_reply_callback` still has a hard 300s `COLLECT_TIMEOUT`, so when codex takes >5 min the aggregated banner reports "미응답" even though the individual codex message now lands. Raising that threshold or making it respect `_CODEX_TURN_TIMEOUT` is a follow-up.

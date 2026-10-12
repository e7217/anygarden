# fix(agent): classify codex failures by side effects and keep the reason (#816)

- Commit: `c0a45ee` (c0a45eeecf389e708f93fdc80632216886f3eff6)
- Author: Changyong Um
- Date: 2026-10-12T22:24:31+09:00
- PR: #816

## Situation

When `codex exec` exited with a non-zero return code, the runtime discarded
both stderr and any `turn.failed` / `error` events from the JSONL stream, so
the cause of the failure was invisible in the local agent log. Worse, the live
runtime (`CodexCliAdapter`) unconditionally set `transient=False`, meaning a
5xx error that occurred after a tool call was never retried — but a 5xx that
happened to arrive after a tool call started could also silently suppress
auto-retry when it might have been safe. Additionally, `stream disconnected`
was absent from the transient-signal list, so a network drop was never
classified as retryable.

## Task

- Parse `turn.failed` / top-level `error` events from the codex JSONL stream to
  extract the provider status code and a redacted human-readable reason.
- Mark a failure **transient** only when (a) no side-effecting item
  (`command_execution`, `file_change`, `mcp_tool_call`, …) has started in the
  current turn, AND (b) the provider status is 429 or 5xx (or a transient
  text signal without an explicit status).
- Surface the redacted reason and stderr tail in the local `structlog` error
  record for diagnosis; never expose them in receipts or to peers.
- Drain stderr in the `CodexRuntime` layer (previously `DEVNULL`) so the pipe
  cannot block the CLI.

## Action

- **`failure_feedback.py`** — Added `CodexFailureTracker` class
  (`runtime/execution/failure_feedback.py`) that observes JSONL events, tracks
  `side_effect_started`, and derives `message`, `status`, `reason`, and
  `code()`. Added `_codex_status()` to extract a numeric HTTP status from either
  a structured JSON body or a `status: NNN` text pattern. Added `redact_secrets()`
  with patterns for bearer tokens, `sk-` API keys, JWTs, and long opaque
  strings. Added `_summarize()` to build a ≤300-char human reason. Added
  `"stream disconnected"`, `"timed out"`, and `"internal server error"` to
  `_TRANSIENT_SIGNALS`. Made `_codex_status` authoritative over text signals
  when a numeric status is present.
- **`codex_cli.py`** — Replaced the unconditional
  `EngineError(..., transient=False)` block with `_nonzero_exit_error()` that
  uses `CodexFailureTracker` to determine transience. Captured `stderr` from
  `proc.communicate()` instead of discarding it.
- **`codex.py`** — Switched `stderr=asyncio.subprocess.DEVNULL` to
  `stderr=asyncio.subprocess.PIPE` and added a `drain_stderr()` coroutine that
  continuously reads the pipe and keeps only the last 2 KB (`_STDERR_KEEP`).
  Added `_log_failure()` helper for structured local logging. Stored the stderr
  tail in a `ContextVar` (`_STDERR_TAIL`) so it is accessible at failure time
  without threading the bytes through every call frame.
- **Tests** — Added `test_failure_feedback.py`, extended `test_local_execution.py`
  and `test_room_execution.py`, and added `test_codex_cli.py` (142 lines) to
  cover transient vs. non-transient classification across side-effect and
  no-side-effect scenarios.

## Decisions

- **Provider status is authoritative over text signals**: if the JSONL body
  carries `"status": 400`, a word like "timeout" inside the error message must
  not make the failure retryable. This avoids false-positive transient
  classification without needing to enumerate every misleading phrase.
- **Side-effect gate blocks transient retry**: once any `command_execution`,
  `file_change`, or `mcp_tool_call` item has started in the turn, the failure
  is never classified as `MODEL_TRANSIENT_FAILURE`. This prevents auto-retry
  from repeating real-world side effects (writing files, running shell
  commands). The cost is that a genuine 429 after a tool call won't be retried
  in-process — acceptable because the caller (lead or execution layer) must
  reconcile anyway.
- **Stderr drained but not forwarded**: the pipe is always consumed to prevent
  the CLI from blocking on a full pipe buffer, but only the tail (2 KB) is
  kept, in a `ContextVar`, for the local error log. Receipts and peers remain
  unaffected.
- **Redaction is conservative**: long opaque strings (≥40 chars) are fully
  redacted. This may over-redact harmless identifiers, but under-redaction
  is the worse failure mode when logs are forwarded to external sinks.
- _Assumption to revisit_: if codex ever emits multiple `turn.failed` events
  in one turn, `code()` returns `None` (ambiguous). This is flagged in the
  code but not yet handled gracefully.

## Result

- Non-zero codex exits now log a structured record with `exit_code`, `status`,
  `reason`, `code`, `transient`, `side_effect_started`, and `stderr_tail`.
- A 5xx provider error with no prior side effect is now retried automatically
  by the bounded auto-retry; a 5xx after a tool call is not.
- `stream disconnected` events are correctly classified as transient.
- 156 new test assertions across four test files validate the classification
  logic and the adapter wiring.

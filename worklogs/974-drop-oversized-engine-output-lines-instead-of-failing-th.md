# fix(runtime): drop oversized engine output lines instead of failing the turn (#974)

- Commit: `eebd869` (eebd869044e2c9d54a06bb599da62720fb40f33e)
- Author: Changyong Um
- Date: 2026-10-29T12:47:07+09:00
- PR: — (issue #974)

## Situation

The Codex and Pi engine runtimes read their JSONL stdout one line at a time via
`asyncio.StreamReader.readline()`, which raises `ValueError` ("Separator is not found, and chunk exceed the limit") when
a line exceeds the reader's internal 1 MiB cap. Because both adapters caught the
exception only at the outer `try/except Exception` block, any oversized line
silently turned the whole agent turn into `invalid_runtime_output` with no log
entry naming the cause. A tool call carrying a large document payload — such as
`publish_project_artifact` with ~5 MB of content — was enough to trigger this
path. Separately, `publish_project_artifact`'s 250,000-character limit was only
declared in the JSON schema and never enforced server-side, so payloads slightly
over the limit could still reach the storage layer and hit an unhandled error
there.

## Task

- Replace `readline()` with a custom async line splitter that tolerates lines
  of any size and does not raise on overrun.
- Drop any single line that exceeds the new 4 MiB limit; count it as a started
  side effect and continue reading; log size and event type only (never the
  payload).
- Log the exception class and a 200-character detail slice whenever a turn
  becomes `invalid_runtime_output`.
- Enforce the artifact content limit (250,000 chars / 768 KiB) server-side and
  return a structured `ARTIFACT_TOO_LARGE` error that tells the agent to split
  the document.
- State the content limit in the `publish_project_artifact` tool description.
- Add tests covering each new behaviour path.

## Action

**`packages/cluster/anygarden/agent/runtime/execution/lines.py`** (new file)
- Implements `engine_lines(stream, *, engine, limit=MAX_LINE)` — an async
  generator that reads in 64 KiB chunks, reassembles lines across chunk
  boundaries, and yields `None` in place of any line that exceeds `limit`.
- Logs `engine_output.line_dropped` with `engine`, `size`, `limit`, and
  `event_type` (extracted from the first 4 KiB of the oversized fragment via a
  regex on `"type"` field) when a line is dropped.
- Exposes `log_invalid_output(engine, exc)` that logs `engine_output.invalid`
  with `error=type(exc).__name__` and `detail=str(exc)[:200]`.
- Sets `MAX_LINE = 4 * 1_048_576` (4 MiB) so reply text and `turn.completed`
  events — capped at 1 MiB each — are never the line that gets dropped.

**`packages/cluster/anygarden/agent/runtime/execution/codex.py`**
- Replaces `while line := await proc.stdout.readline()` with
  `async for line in engine_lines(proc.stdout, engine=ENGINE)`.
- Handles `line is None` by setting `failures.side_effect_started = True` and
  continuing.
- Changes `except Exception:` to `except Exception as exc:` and calls
  `log_invalid_output(ENGINE, exc)` before setting `invalid_runtime_output`.
- Removes the now-redundant local `MAX_LINE` constant (moved to `lines.py`).

**`packages/cluster/anygarden/agent/runtime/execution/pi.py`**
- Same refactor as `codex.py`: `engine_lines` loop, `None`-check sets
  `effects["started"] = True`, `log_invalid_output` on exception.
- Removes local `MAX_LINE`.

**`packages/cluster/anygarden/project_executions/artifacts.py`**
- Defines `MAX_CONTENT_CHARS = 250_000` and `MAX_CONTENT_BYTES = 768 * 1024`.
- In `publish_artifact`, raises `ExecutionConflict("ARTIFACT_TOO_LARGE", ...)`
  if either limit is exceeded, including the limits in the error message so the
  agent knows how to split.
- Replaces the inline `768 * 1024` magic number in `save_bytes` call with
  `MAX_CONTENT_BYTES`.

**`packages/cluster/anygarden/mcp/project_tools.py`**
- Imports `MAX_CONTENT_CHARS` and `MAX_CONTENT_BYTES` from `artifacts.py`.
- Appends a sentence about the content limit to the `publish_project_artifact`
  tool description.
- Adds `"publish_project_artifact"` to the set of tool names whose
  `ExecutionConflict` is returned as a structured tool error (rather than
  re-raised).

**Tests added:**
- `tests/agent/test_execution/test_engine_lines.py`: unit tests for normal
  splits, oversized-line drop, unterminated oversized tail, and exact-limit
  boundary.
- `tests/agent/test_execution/test_local_execution.py`: integration tests for
  `big-tool-call` (oversized line dropped, turn still succeeds) and
  `_collect`-injection (exception class appears in log). Updates `"huge"`
  expected outcome from `invalid_runtime_output` to `missing_terminal_event`.
- `tests/agent/test_execution/test_pi_runtime.py`: same two tests for the Pi
  adapter.
- `tests/test_project_execution_flow.py`: end-to-end test that both char and
  UTF-8 byte limits return `ARTIFACT_TOO_LARGE` and that a small artifact still
  publishes after the rejections.

## Decisions

- **4 MiB line limit rather than 1 MiB**: The 1 MiB cap matches the reply-text
  limit, but a tool-call event wraps that text inside a JSON envelope, pushing
  the line over 1 MiB even for valid content. 4 MiB leaves enough headroom for
  any well-formed event while still bounding memory consumption per line.
  Alternatives considered: raising the `StreamReader` limit (would still raise
  on truly unbounded lines and is harder to log); parsing incrementally without
  a line model (significantly more complex with no benefit given JSONL protocol).

- **Drop-and-continue instead of fail-fast**: The overrun was almost always
  caused by a tool call with an oversized argument, not by the engine emitting
  corrupt data. Dropping the line lets the following lines —
  including the reply and `turn.completed` — arrive so the turn can succeed.
  Counting the dropped line as a started side effect keeps timeout
  classification fail-closed (a timeout after it is never retried).

- **Server-side enforcement of the artifact limit**: The schema `maxLength` was
  only a client hint; agents could bypass it by constructing calls directly.
  Enforcing at the service layer and returning a structured `ExecutionConflict`
  is consistent with how other limits (budget exhaustion, conflict) are reported
  and gives the agent a clear instruction to split the document. The limit value
  (250,000 chars / 768 KiB) was already the storage cap — this change merely
  makes it explicit and names it.

## Result

- Agent turns whose stdout contains a line over 4 MiB no longer end as
  `invalid_runtime_output`; the oversized line is skipped and reading continues.
- When a turn does become `invalid_runtime_output`, the log now records the
  exception class and a truncated message, making root-cause analysis
  straightforward.
- `publish_project_artifact` returns a structured `ARTIFACT_TOO_LARGE` error
  with the exact limit for both the character and byte dimensions, rather than
  crashing the storage layer or returning a generic 500.
- Four new test files / extended test suites cover the above paths.
- Follow-up issue #975 filed: `feat(artifacts): 큰 파일을 경로로 게시하고 ID로 넘기기`
  to explore publishing large files by path and referencing them by ID.

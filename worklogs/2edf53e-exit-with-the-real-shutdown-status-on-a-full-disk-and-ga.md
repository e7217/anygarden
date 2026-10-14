# fix: exit with the real shutdown status on a full disk and gate Pi bumps on the safe-event review

- Commit: `2edf53e` (2edf53eda40c54da115a0549b877b6e0ed69b23c)
- Author: Changyong Um
- Date: 2026-10-14T12:31:12+09:00
- PR: #823

## Situation

After #822 made structlog writes best-effort on a full disk, bytes that
failed to write remained in `sys.stdout`/`sys.stderr` stream buffers.
Python's interpreter exit flush then tried to drain them; on a full disk
that flush raised `ENOSPC` and the process exited 120 — masking the real
shutdown outcome. Both a clean `anygarden stop` (exit 0) and a genuine
cleanup failure (should exit 1) appeared identically as exit 120 to any
supervising process. In parallel, the Pi safe-event list (`_SAFE_EVENTS`
and companions) had no recorded link to the Pi version it was reviewed for,
so upgrading the pinned Pi engine could silently invalidate the fail-closed
timeout logic without anyone noticing.

## Task

- (NODE-10) Ensure `anygarden start` / the machine daemon exit with the
  correct status (0 for clean, 1 for genuine failure) even when a full disk
  leaves unflushable bytes in stdout/stderr at shutdown.
- (EXE-10) Record which Pi version the safe-event list was reviewed against
  and make CI fail automatically when the managed Pi pin drifts away from it.

## Action

**`packages/cluster/anygarden/observability/logging.py`**
- Added `settle_standard_streams()`: flushes `sys.stdout` and `sys.stderr`;
  if a flush fails (ENOSPC), reopens the fd on `/dev/null` via `os.dup2`
  so pending bytes drain harmlessly and the interpreter's exit flush
  succeeds.

**`packages/cluster/anygarden/cli.py`**
- Introduced `_NodeExitError(click.ClickException)` whose `.show()` calls
  `settle_standard_streams()` before returning (best-effort message print,
  disk-full write absorbed).
- Replaced all three `click.ClickException` raises in `start_node` with
  `_NodeExitError`, and added a `settle_standard_streams()` call on the
  clean-exit path so exit 0 is also safe.

**`packages/cluster/anygarden/machine/cli.py`**
- Wrapped the `click.echo("Daemon stopped.")` in the `KeyboardInterrupt`
  handler in a try/except OSError to absorb full-disk write failures.
- Called `settle_standard_streams()` unconditionally after `asyncio.run`.

**`packages/agent/anygarden_agent/runtime/execution/pi.py`**
- Added `PI_SAFE_EVENTS_REVIEWED_FOR = "0.85.1"` constant documenting the
  Pi version against which `_SAFE_EVENTS`, `_SAFE_MESSAGE_ROLES`, and
  `_SAFE_CONTENT_BLOCKS` were last reviewed.

**`packages/agent/tests/test_execution/test_pi_runtime.py`**
- `test_safe_event_list_was_reviewed_for_the_pinned_pi_version` (`@req
  EXE-10`): asserts `PI_SAFE_EVENTS_REVIEWED_FOR == ENGINE_VERSION`.

**`packages/cluster/tests/machine/test_engines_managed.py`**
- `test_pi_safe_event_list_reviewed_for_managed_pin` (`@req EXE-10`):
  asserts `PI_SAFE_EVENTS_REVIEWED_FOR == managed.PI_PINNED_VERSION`.

**`packages/cluster/tests/test_local_node.py`**
- `test_real_cli_exit_status_survives_unflushable_stdout` (`@req NODE-10`,
  parametrized clean/cleanup-fails): points fds 1/2 at `/dev/full` before
  stop, checks exit code is 0 (clean) or non-zero (failure) — never 120.

## Decisions

The core design choice for NODE-10 was where to call `settle_standard_streams()`:

- **Option A — monkey-patch `sys.excepthook` / `atexit`**: would cover all
  exit paths but is fragile and affects unrelated test processes in the same
  interpreter.
- **Option B — call at every explicit exit site**: more surgical; the only
  exit paths that matter are the two CLI entry points (`start_node` and the
  machine daemon's `run` command). Chosen because it is local, auditable, and
  avoids global interpreter side-effects.
- **Option C — wrap streams in a custom class that swallows write errors**:
  would require replacing `sys.stdout`/`sys.stderr` globally and complicates
  debugging; rejected.

`_NodeExitError` was added to DRY up the three `ClickException` raise sites
while ensuring the stream-settling happens even on error paths where the
caller may not reach the clean-exit `settle_standard_streams()` call. The
`.show()` override keeps the existing message-printing behaviour (best effort)
consistent with `BestEffortPrintLogger`.

For EXE-10 a constant (`PI_SAFE_EVENTS_REVIEWED_FOR`) was preferred over a
comment because a test can assert on a constant; it cannot assert on a comment.
Two tests guard the constraint from both the agent-side and the managed-engine
side so neither can diverge independently.

## Result

- `anygarden stop` (clean shutdown, full disk): exits 0, not 120.
- `anygarden stop` (cleanup failure, full disk): exits 1, not 120.
- Machine daemon stops cleanly on `SIGINT` even with full disk: exits 0.
- CI fails with a clear message when the managed Pi pin is bumped without
  updating `PI_SAFE_EVENTS_REVIEWED_FOR`.
- 157 new lines of test coverage in `test_local_node.py` reproducing the
  `/dev/full` scenario end-to-end.
- PR #823 opened as a follow-up to #822.

# fix(node): survive a disk-full blip and never stay half-dead

- Commit: `b82873c` (b82873c8fc6d10c58bc0b9a0f60bbcff08047f99)
- Author: Changyong Um
- Date: 2026-10-13T18:06:40+09:00
- PR: —

## Situation

During a core-suite benchmark run the host disk briefly filled up. structlog's `PrintLogger` writes via `print(..., flush=True)`, which raises `OSError(ENOSPC)` on a full disk. That exception escaped the `#772` transient-failure tolerance in `_handle_frame`, so the very first error-path log call permanently marked local execution as failed. `_failed` then tried to call the shutdown callback, but since that call happened *after* setting `self.failed = True` and also raised (another log write), the callback was never invoked. As a result the node continued serving the healthz API reporting `"status": "ok"` while no new agent frames could ever be processed — a half-dead state that lasted over an hour.

## Task

- Prevent ENOSPC (or any log-write error) from permanently failing local execution during transient I/O outages.
- Ensure `_failed` triggers the shutdown callback reliably before attempting any log write.
- Expose the fatally-failed state through `GET /healthz` as a 503 so probes and supervisors can detect it.
- Move the `stop_requested()` check inside the transient-failure tolerance so an intermittent `OSError` there is counted as a maintenance failure rather than an instant fatal.
- Count consecutive frame failures toward a new `MAX_FRAME_FAILURES` limit so a *sustained* failure (disk stays full) still eventually stops the node.
- Document the requirement as NODE-10 in `docs/requirements.md`.

## Action

- **`packages/cluster/anygarden/node/execution.py`**
  - Added `_log_best_effort(level, event, **kwargs)` (line ~22): wraps every `log.*` call in a bare `try/except Exception: pass` so a full-disk write never escapes error paths.
  - Added `MAX_FRAME_FAILURES = 40` class constant (line ~75): a run of 40 consecutive frame failures with no success in between triggers `_failed`, mirroring the existing `MAX_MAINTENANCE_FAILURES`.
  - Refactored `_handle_frame` to return `Exception | None` instead of `None` (line ~190): callers can now count failures without re-raising.
  - Added `_request_shutdown(self)` helper (line ~220): isolated the shutdown-callback invocation so `_failed` can call it inside a `try/finally` before logging.
  - Refactored `_failed` (line ~226): now calls `_request_shutdown()` first (inside `try/finally`) then logs via `_log_best_effort`; a log write failure no longer leaves the node alive.
  - Refactored `_consume` (line ~240): tracks consecutive failures with a counter; resets on success; raises after `MAX_FRAME_FAILURES` consecutive errors.
  - Moved `stop_requested()` check inside the maintenance `try/except` block (line ~258) so transient `OSError` from that file read counts as a maintenance failure, not a fatal.
  - Replaced all bare `log.warning/log.error/log.exception` calls in error paths with `_log_best_effort`.

- **`packages/cluster/anygarden/app.py`**
  - `GET /healthz` handler (line ~1681): reads `app.state.local_execution.failed`; if `True`, adds `"local_execution": "unhealthy"` and sets `critical_down = True` → returns 503. Absent in non-integrated mode.

- **`packages/cluster/tests/test_local_node.py`**: 306-line test suite added covering the new behaviour (NODE-10 scenarios: ENOSPC survival, consecutive frame failure limit, `_failed` ordering, healthz 503 after failure).

- **`docs/requirements.md`**: added NODE-10 row.

## Decisions

- **Best-effort logging vs. structured error propagation**: the alternative was to propagate `OSError` from log writes back to the caller and treat it as a frame failure. Rejected because the log call already happens *inside* an exception handler; re-raising would conflate two separate errors and complicate the tolerance logic. Swallowing the log write is acceptable: losing a line beats losing the node.

- **`MAX_FRAME_FAILURES` same value as `MAX_MAINTENANCE_FAILURES` (40)**: could have been higher (more tolerance) or lower (faster fail). Matching the existing constant keeps the two limits symmetric and easy to reason about; the value can be tuned if benchmarks show it's too aggressive or too lenient.

- **Shutdown-before-log ordering in `_failed`**: the alternative was to log first, then shut down. Rejected because the original bug was exactly this: the log call raised before shutdown ran, leaving the node alive. Shutdown is idempotent and cheap; losing the log line is acceptable.

- **`stop_requested()` inside the tolerance**: previously outside so an error there would immediately stop the loop. Moving it inside means a transient `OSError` reading the stop-file is tolerated up to `MAX_MAINTENANCE_FAILURES`. If the stop-file is perpetually unreadable the node will eventually fail anyway; the risk of a delayed shutdown is lower than the risk of false-positive termination from a momentary I/O blip.

## Result

- A brief disk-full condition no longer permanently kills local execution; frames resume processing once space is freed.
- A sustained failure (>= 40 consecutive frame errors) still causes the node to call its shutdown callback and stop.
- `GET /healthz` returns 503 with `"local_execution": "unhealthy"` once execution has fatally failed, so probes/supervisors detect the half-dead state.
- `docs/requirements.md` documents NODE-10.
- 306 lines of new tests cover the new behaviour paths.

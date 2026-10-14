# fix: shut down cleanly on a full disk, count transient retries exactly, detect side effects fail-closed

- Commit: `decb0da` (decb0daa6ac18237317219aeef65578e09eaa68e)
- Author: Changyong Um
- Date: 2026-10-14T11:20:37+09:00
- PR: —

## Situation

Three independent correctness gaps existed in the execution and recovery layer:

1. **NODE-10 / full-disk shutdown**: `structlog.PrintLogger` flushes every write synchronously; on a full disk this raised `OSError(ENOSPC)` in the middle of node shutdown cleanup paths (spawner `drain_complete`, turn recovery). The lifespan then saw an unconfirmed cleanup and marked the node recovery-required, preventing the next supervisor restart until manual intervention.

2. **EXE-10 / transient retry over-count**: The transient retry budget was derived by counting `AgentTurnAttempt` rows in state `interrupted` with `reason == MODEL_TRANSIENT_FAILURE`. A fence that recovery later *closed* without actually scheduling a retry (gate revoked, admission denied, budget exhausted) was still counted, making the retry budget appear spent one attempt too early.

3. **EXE-10 / side-effect detection open by default**: Both the Pi and codex execution trackers used an allowlist-like logic for known events but defaulted to *safe* for any unrecognised event type. A newer runtime version adding an unknown event type that carries real work would incorrectly classify a timed-out turn as having no side effects, enabling a spurious transient retry.

## Task

- Replace `structlog.PrintLogger` with a best-effort variant that swallows `OSError`/`ValueError` on write, used on all logging paths.
- Add an `AgentTurnAttempt.transient_retry` boolean column (migration 093) set *only* when recovery actually schedules a retry; rewrite the retry budget counter to use it.
- Flip the Pi and codex side-effect detectors to fail-closed: only explicitly known-safe event types keep a timeout/failure transient; anything else is treated as a potential side effect.

## Action

**Logging (NODE-10)**
- `packages/cluster/anygarden/observability/logging.py:43–72`: Added `BestEffortPrintLogger` (subclasses `PrintLogger`, all write methods wrap `super().msg()` in `except (OSError, ValueError): pass`) and `BestEffortPrintLoggerFactory`. Both `configure_logging` and `machine/cli.py:main` now use `BestEffortPrintLoggerFactory`.
- `packages/cluster/anygarden/node/execution.py:380`: Changed `log.info(...)` → `_log_best_effort("info", ...)` on the turn-recovery confirmation log line so a failed write cannot undo confirmed cleanup.
- `packages/cluster/tests/test_local_node.py:1447–1590`: End-to-end test: patches `PrintLogger.msg` to raise `ENOSPC` once the `marker` file appears (just before `stop`), verifies the node exits cleanly with `node-owner.json.state == "stopped"`, and confirms the supervisor can restart on the same data dir.

**Transient retry accounting (EXE-10 — schema)**
- `packages/cluster/anygarden/db/migrations/versions/093_turn_attempt_transient_retry.py`: New migration adds `transient_retry BOOLEAN NOT NULL DEFAULT FALSE` to `agent_turn_attempts`. Backfill: old `interrupted/MODEL_TRANSIENT_FAILURE` rows get `transient_retry=1` only when a later attempt exists (best available evidence that a retry was scheduled).
- `packages/cluster/anygarden/db/models.py:2141–2145`: Added `transient_retry: Mapped[bool]` to `AgentTurnAttempt`.
- `packages/cluster/anygarden/turns/service.py:66,74–78,1793–1797`: `is_transient_retry_attempt` now returns `bool(attempt.transient_retry)`. `transient_retries_used` filters on `transient_retry.is_(True)` instead of state/reason. `recover_stalled_turns` sets `transient_retry=True` on the fenced attempt at the exact moment it schedules the retry — and nowhere else.
- `packages/cluster/tests/test_durable_turns.py:1060–1089`: New test `test_exhausting_transient_fence_spends_no_transient_retry` exhausts the budget and asserts `transient_retries_used == MAX_TRANSIENT_RETRIES`, not `MAX_TRANSIENT_RETRIES + 1`.

**Side-effect detection fail-closed (EXE-10 — codex)**
- `packages/agent/anygarden_agent/runtime/execution/failure_feedback.py:131–138,229–240`: Added `_FRAMING_EVENTS` and `_ITEM_EVENTS` frozensets. The event handler now: processes known item events via `_ITEM_EVENTS`, treats any event outside `_FRAMING_EVENTS` as a side effect (previously unknown top-level events were silently ignored).
- `packages/agent/tests/test_execution/test_failure_feedback.py:208–242`: New parametrised tests for `EXE-10`: unknown item types, missing item type, unknown top-level event all assert `side_effect_started=True`; a full sweep of `SIDE_EFFECT_FREE_ITEMS` stays `side_effect_started=False`.

**Side-effect detection fail-closed (EXE-10 — Pi)**
- `packages/agent/anygarden_agent/runtime/execution/pi.py:41–61,438–448`: Replaced `_TOOL_EVENTS`/`_starts_tool` with `_SAFE_EVENTS`, `_MESSAGE_EVENTS`, `_SAFE_MESSAGE_ROLES`, `_SAFE_CONTENT_BLOCKS` and `_may_have_side_effect`. The new function returns `True` (side effect) for any event not in `_SAFE_EVENTS`, for malformed messages, for unknown roles, for non-text/thinking content blocks, and for parse errors. Invalid JSON lines also set `effects["started"] = True`.
- `packages/agent/tests/test_execution/test_pi_runtime.py:90–109,425–428`: Two new fake-Pi scenarios (`safe-stall`: only safe events before stall → `TIMEOUT_STOPPED` transient; `unknown-stall`: one `extension_action_start` → `TIMEOUT_AFTER_SIDE_EFFECT` non-transient).

**Migration head update**
- `packages/cluster/tests/test_federation_trust.py:906`: Updated expected head from `092_task_input_requests` to `093_turn_attempt_transient_retry`.

## Decisions

- **`BestEffortPrintLogger` wraps, not replaces `PrintLogger`**: subclassing keeps the existing structlog configuration surface intact; swapping the factory is a one-line change per call site. Alternatives considered: patching the factory globally vs. per call site (chosen: per call site to keep worker/test logging unaffected); silencing at the OS level with `os.write` redirection (rejected: too invasive, masks real I/O errors in unrelated paths).
- **Separate `transient_retry` column vs. deriving from state/reason**: Adding an explicit marker makes the semantics independent of other state machine transitions — recovery can close an attempt for reasons unrelated to transient retries without affecting the budget. Alternative of using a separate retry-event table was considered but rejected as over-engineering for a single boolean.
- **Fail-closed for side effects**: switching from "fail open" (unknown events are safe) to "fail closed" (unknown events are effects) is a breaking change for any future Pi/codex version that adds a new safe event type, but it's the safe default: a mistaken non-transient classification wastes one manual retry; a mistaken transient classification risks re-running destructive work. Future safe event types can be added to the frozensets as they're identified. (Unclear from available context: whether there is a version-negotiation mechanism that could automate allowlist expansion.)
- **Backfill in migration 093**: backfilling via `EXISTS (later attempt)` is a heuristic — it cannot recover cases where the transient fence was the *last* attempt before a permanent failure. The comment documents this limitation explicitly.

## Result

- A node on a disk that fills during or after `stop` now shuts down with `node-owner.json.state == "stopped"` and the supervisor can restart it immediately (NODE-10). Verified by end-to-end test that patches all `PrintLogger.msg` variants to raise `ENOSPC`.
- The transient retry budget is counted from `transient_retry=True` markers set atomically at the moment recovery schedules a retry. Fences closed without a retry no longer consume budget (EXE-10). Test confirms `transient_retries_used == MAX_TRANSIENT_RETRIES` after budget exhaustion.
- Both Pi and codex side-effect detectors are fail-closed: only events in their explicit safe-set keep a timeout/failure transient. Tests cover unknown item types, unknown top-level events, and parse errors for the codex path; unknown Pi event types and malformed JSON lines for the Pi path.
- 15 files changed, 496 insertions, 33 deletions; migration head advances to `093_turn_attempt_transient_retry`.

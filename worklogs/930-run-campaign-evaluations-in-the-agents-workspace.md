# feat(machine): run campaign evaluations in the agent's workspace (#930)

- Commit: `b0fce18` (b0fce18c661c3ac6466efd82179947ee04a34c0a)
- Author: Changyong Um
- Date: 2026-10-26T...+09:00
- PR: #930

## Situation

Campaign evaluation (§3.7 of plan-930-metric-campaigns.md) requires the machine daemon — not the agent — to execute the evaluator command and report a score. Before this commit, the machine had no mechanism to receive, execute, or respond to evaluation requests: there were no protocol frames for it, no path-safety checks for the agent workspace, and no runner that could isolate env, enforce timeouts, and hash pin files. The server therefore had nowhere to send evaluation work after a cycle completed.

## Task

Implement the machine-side evaluation runner for campaign cycles (Phase 3 of the plan), covering:

- New WebSocket frames: `campaign_eval_request`, `campaign_eval_ack`, `campaign_eval_result`
- `cwd` and every pin path must resolve strictly inside the agent's workspace (no `..`, no escaping symlinks)
- Pins hashed before running; a mismatch reports `invalid` without executing the command
- Command runs in a new process group (`start_new_session=True`) with an allowlisted env — no agent token, no engine secrets
- Timeout kills the entire process group (SIGTERM → grace → SIGKILL)
- Last non-empty stdout line must be JSON containing the numeric `score_key` value and a boolean `valid`; any deviation is an error
- One job per machine at a time: a second concurrent request is refused as `busy` (server retries); a finished job's result is re-sent on repeat requests
- Full test coverage for the runner in `test_campaign_eval.py`

## Action

**`packages/cluster/anygarden/machine/campaign_eval.py`** (new file, ~459 lines):
- `workspace_root()` / `resolve_inside()`: path containment checks using `Path.resolve()` and `is_relative_to()`, refusing symlinks that escape the workspace.
- `pin_digest()` / `compute_pins()`: sha256 over individual files or sorted directory trees, bounding traversal at 10 000 files.
- `parse_score_line()`: extracts the last non-empty stdout line, parses JSON, validates `score_key` is a finite non-boolean number and `valid` is a bool, then copies a bounded subset of keys into `parsed`.
- `eval_environment()`: allowlist of 13 env keys (`PATH`, `HOME`, locale, temp dirs, Windows process vars); adds `ANYGARDEN_CAMPAIGN_EVAL=1` and `PYTHONUNBUFFERED=1`.
- `run_eval()`: orchestrates workspace validation → pin check → `asyncio.create_subprocess_exec` → concurrent stdout/stderr drain → `wait_for` timeout → process-group kill → score parsing; never raises for request or command errors.
- `CampaignEvalRunner`: singleton runner per machine; `submit()` checks a 16-entry LRU result cache (re-send finished), rejects new jobs when one is running (`busy`), otherwise schedules `_run()` as an asyncio task.

**`packages/cluster/anygarden/machine/protocol/frames.py`** (additions):
- Added `CampaignEvalRequestFrame`, `CampaignEvalAckFrame`, and `CampaignEvalResultFrame` Pydantic models for the three-message job handshake.

**`packages/cluster/anygarden/machine/daemon.py`** (additions):
- Wired the new `campaign_eval_request` frame type into the daemon's dispatch loop, connecting it to `CampaignEvalRunner.submit()` and responding with ack/result frames.

**`packages/cluster/tests/machine/test_campaign_eval.py`** (new file):
- Tests for: successful parse with multi-line output and correct cwd, timeout and process-group kill, pin mismatch → `invalid`, path-escape rejection, bad JSON output, missing `score`/`valid` fields, `busy` refusal, cached result re-send.

## Decisions

**Why refuse a second job rather than queue it (plan §3.7):**
- Option A (queue): simpler for the server but a queued job can wait behind a 6-hour evaluation and miss its `eval_deadline_at`; the server would mark it `timed_out` while the job is still waiting, wasting machine resources.
- Option B (refuse as busy, server retries): the server only starts counting the deadline once the job actually runs, so a short evaluation never blocks behind a long one.
- **Chosen: B.** The plan explicitly states this reasoning.

**Why process group (`start_new_session=True`) rather than killing only the shell:**
- The shell (`/bin/sh -c ...`) may exit while spawned children still hold stdout pipes. Killing only the shell PID leaves grandchildren running and the pipes open indefinitely.
- `os.killpg` on the new session's PGID guarantees every descendant gets the signal. Windows falls back to `terminate_tree`.

**Why allowlist env rather than inherit and strip sensitive keys:**
- Stripping requires knowing every secret name in advance; new env vars added to the machine process (e.g. a future engine credential) would silently leak.
- An allowlist of 13 well-known keys fails safe: unknown vars are never passed regardless of future changes.
- The plan (§3.7) explicitly mandates this approach.

**Why LRU result cache (16 entries) rather than discarding after send:**
- The first `campaign_eval_result` frame may be lost on a disconnect (plan §3.7 states "a finished job's result is re-sent").
- 16 entries is enough for any realistic burst of rapid evaluations; old entries evict automatically.

**Isolation boundary (explicit non-decision):**
- The runner shares the machine user and filesystem with the agent. Pin hashing prevents accidental or trivial modification of evaluated artefacts, but does not prevent a malicious evaluator from reading agent files. Full isolation is deferred to #931 as explicitly stated in the plan.

## Result

- Machine daemon can now receive `campaign_eval_request` frames, validate paths and pins, execute the evaluator command in the correct workspace subdirectory with a minimal environment, and return a structured `campaign_eval_result` frame.
- The server can safely send evaluation requests after cycle completion and rely on busy-refusal + re-send semantics to handle reconnects and concurrent ticks.
- Test suite in `test_campaign_eval.py` covers the main success path, timeout, pin mismatch, path escape, parse errors, busy refusal, and result cache re-send.
- Still pending (Phase 3 remainder): `machine_bus.request_campaign_eval`, the server-side eval result handler, and the `eval_deadline_at` timeout on the server side.

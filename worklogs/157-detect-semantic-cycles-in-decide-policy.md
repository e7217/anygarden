# feat(agent): detect semantic cycles in decide_policy (#157 Phase B)

- Commit: `a66d31b`
- Author: Changyong Um
- Date: 2026-04-19
- PR: pending
- Issue: #157 (umbrella)

## Situation

Phase A (PR #160) capped prefix-abuse loops with a consecutive-task-init guard, but that only covers the shape where the same agent keeps emitting `[ROOM_QUERY]` / `[DELEGATED]`. Two agents can still trap each other by exchanging the *exact same* non-task-init content turn after turn. The 2026-04-19 deep-research report cites this as the $47K production pattern (agent A/B ping-pong 11 days undetected, fixbrokenaiapps.com 2025); arXiv:2511.10650 reports F1=0.72 on real production traces with a hash-based detector and declares it the missing layer next to turn counters.

## Task

- Add a semantic cycle detector: hash the (sender, content-prefix) fingerprint of each incoming message and look for repeats within a small window.
- Keep false positives low — short replies (`ok`, `네`, `done`) legitimately repeat and must be excluded.
- Wire the detector into `decide_policy` ahead of the direct-mention rule so an @-mention chain can't override the guard.
- Surface `room_id` on the handler-facing message dict so the detector can look up the per-room buffer (adapters already read `msg.get("room_id")` but the field was previously unset by `_process_frame`).
- Preserve Phase A behaviour — both PRs stack cleanly.

## Action

- `packages/agent/doorae_agent/integrations/cycle_guard.py` (new)
  - `hash_content(content)` — SHA1 over the first 64 casefolded chars, truncated to 16 hex. Returns `None` for content < 16 chars so short repeats don't feed the detector.
  - `is_cycle_detected(msg, recent, *, window=6, min_repetitions=2)` — flags True when the (sender, hash) pair from `msg` appears ≥ `min_repetitions` times in the last `window` entries of `recent`.
- `packages/agent/doorae_agent/client.py`
  - Added `_recent_msgs: dict[str, collections.deque[dict[str, str]]]` with `maxlen=10` (`_recent_msgs_maxlen`) to `__init__`.
  - New `_record_recent_message(room_id, msg)` helper appends a `{sender, hash}` entry; short content / missing sender short-circuit.
  - `_process_frame` calls `_record_recent_message` at the top of the message branch *before* any early-return filters, so self-echo and nonce-echo frames also feed the detector.
  - After the turn-counter block, `data.setdefault("room_id", room_id)` is injected before handler dispatch — adapters already read `msg.get("room_id")` (see `claude_code.py`), this makes the field authoritative.
- `packages/agent/doorae_agent/integrations/base.py`
  - Imported `cycle_guard.is_cycle_detected` and `structlog`; module-level `logger`.
  - Added rule 2d in `decide_policy`, placed between the room_query guard (rule 2b) and the direct-mention rule (rule 3). On cycle hit it logs `decide_policy.cycle_detected` at WARN with `room_id` and `sender`, then returns `MessagePolicy.SKIP`.
- `packages/agent/tests/test_cycle_guard.py` (new) — 16 tests covering hash correctness (length, casefold, 64-char prefix, short-content None) and detector edge cases (no history, single match below threshold, multi-sender isolation, window truncation, custom `min_repetitions`, deque iterables).
- `packages/agent/tests/test_client.py::TestRecentMessagesBuffer` — 6 integration tests asserting the ring buffer is populated from `_process_frame`, short content is skipped, welcome frames are skipped, per-room isolation holds, maxlen caps at 10, and `room_id` is injected on handler-facing messages.
- `packages/agent/tests/test_integrations/test_should_respond.py::TestCycleDetectionInDecidePolicy` — 5 tests asserting that cycle pre-empts a direct mention (SKIP beats RESPOND), cross-sender reuse is not a loop, short content never trips, missing `room_id` disables the rule (legacy compat), and a single prior match isn't a loop.
- `_make_client` helper gains a `recent_msgs` parameter (default `{}`), preserving legacy tests while letting new ones pre-load history.

## Result

- Phase B tests: 27 new (16 + 6 + 5) all pass.
- `uv run pytest packages/agent/` — 189 passed, 1 pre-existing failure (`test_openai::test_integrate_registers_handler`, unrelated `OPENAI_API_KEY` flake).
- `uv run pytest packages/cluster/` — 587 passed.
- The guard fires with `decide_policy.cycle_detected` WARN log — operators can correlate with the turn-counter and reset-guard logs from Phase A.
- Rule 2d sits *before* the mention rule, so a determined @-chain cannot force an agent to repeat itself. Phase A's `max_agent_turns` remains the catch-all, Phase B is the per-content layer, Phase C's token telemetry (next) becomes the observability layer.

# fix(agent): surface engine turn failures instead of silent response loss (#422)

- Commit: `8f83097` (8f83097 on branch fix/422-agent-silent-failure-surfacing)
- Author: Changyong Um
- Date: 2026-06-08
- PR: #422

## Situation

A user sent a message to a codex agent and saw it type briefly, then nothing.
Investigation (using the #420 lifecycle logs + codex session sqlite) found the
real cause was the agent's effective model `gpt-5.5` being unsupported by the
installed codex-cli 0.137.0 (OpenAI 400 "requires a newer version of Codex").
But the deeper defect was that this failure was **invisible**: every engine
adapter swallowed turn failures (`except: return None`), and
`RoomHandlerSupervisor` recorded the None as `outcome=ok` with an empty
response and no send. So model errors, tool failures and adapter timeouts all
collapsed into a silent "ok" — the silent response loss the #420 design set
out to eliminate, defeated at the adapter layer.

## Task

- Make engine turn failures reach the supervisor so they surface as
  `outcome=failed` (and adapter timeouts as `timeout`) instead of `ok`.
- Notify the user on failure so silence never reads as success.
- Distinguish a genuine "no-reply" from an "empty because the engine failed".
- Apply across all four adapters (codex/claude_code/gemini_cli/openhands).
- Constraints: no wire-protocol change; don't break legitimate proactive
  no-reply; keep ambient ingestion (which never hits the supervisor) intact.

## Action

- `runtime/handler_wrapper.py`: added `EngineError` / `EngineTimeoutError`
  and `_TIMEOUT_NOTICE` / `_FAILED_NOTICE`. `_run` now catches
  `EngineTimeoutError → outcome=timeout`; bare exceptions (incl. `EngineError`)
  → `failed`. Added a reclassification: a tracked turn (`request_id` present)
  that yields no text becomes `failed`. The send block notifies the user on
  both `timeout` and `failed`.
- `integrations/codex.py:411-` — turn timeout raises `EngineTimeoutError`;
  the catch-all raises `EngineError` (with an `except EngineError: raise`
  guard so the timeout isn't reclassified); thread eviction preserved.
- `integrations/claude_code.py:189-`, `gemini_cli.py:247-` (+ `_call_gemini`
  timeout at :386-), `openhands_engine.py:300-,315-` — same swallow→raise
  conversion; gemini conversation rollback and openhands logging preserved.
- Tests: `test_handler_supervisor.py` — updated the failed/empty tests to the
  new notify behavior and added EngineError / EngineTimeoutError / tracked-empty
  / proactive-empty cases. `test_codex.py` and `test_gemini_cli.py` —
  timeout/exception tests now assert `pytest.raises(...)` while still checking
  thread eviction / conversation rollback.

## Decisions

- **Chose C (typed exceptions + empty policy) over A (propagate-only) and B
  (typed result object)** — per `.tmp/plan-422-agent-silent-failure-surfacing.md`.
  - A (just stop swallowing so the supervisor's existing `except Exception →
    failed` fires) was insufficient: the observed gpt-5.5 failure plausibly
    returns empty *without* raising (2s turn, `ok`, the 400 logged at
    `session_startup_prewarm`), so exceptions alone wouldn't catch it.
  - B (change `on_message` to return `EngineResult(text,status)`) was rejected
    as over-scoped: the information was lost in the adapters' broad `except`,
    not in the return type; a full contract change across 4 adapters + all
    tests wasn't worth it when the supervisor already classifies outcomes.
  - Decisive observation: `decide_policy` routes SKIP→drop, INGEST_ONLY→
    `ingest_context`, and **only RESPOND reaches `supervisor.dispatch`**. So a
    supervised turn returning empty is never a legitimate ambient no-reply — it
    means the engine was asked to answer and didn't. That makes "tracked empty
    = failed" safe and resolves the gpt-5.5 case regardless of raise-vs-empty.
  - Assumptions to revisit: that no RESPOND turn legitimately returns empty
    (if some agent intentionally stays silent on a mention, it would now get a
    "couldn't generate a response" notice); and that proactive/untracked empty
    (request_id is None) should stay a silent ok (preserved).

## Result

- `uv run pytest packages/agent` → 387 passed; `ruff check` clean on all
  changed files. New behavior covered: EngineError→failed+notice,
  EngineTimeoutError→timeout+notice, tracked-empty→failed+notice,
  proactive-empty→silent ok.
- No cluster/wire change (failed/timeout already in the lifecycle enum; the
  notice is an ordinary agent message).
- The original gpt-5.5 symptom was separately unblocked by switching the agent
  to `gpt-5.4`; this change makes any such failure visible going forward.
- Pending/out of scope: codex-specific SDK-event inspection to label the exact
  error on a no-exception empty turn (the supervisor's empty→failed reclass
  already surfaces it generically); anygarden's `gpt-5.5` default vs installed
  codex mismatch (separate config concern).

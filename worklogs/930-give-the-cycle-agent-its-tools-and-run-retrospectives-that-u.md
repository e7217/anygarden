# feat(campaigns): give the cycle agent its tools and run retrospectives that update its instructions (#930)

- Commit: `e2d3835`
- Author: Claude Opus 5.5
- Date: 2026-10-26
- PR: #930

## Situation

The campaign system had a lifecycle and evaluation framework (Phases 1–4) but the cycle agent lacked the MCP tools to record its own attempts, schedule its next wakeup, or flag blockages during a run. Additionally, there was no retrospective mechanism to let the system learn from completed campaigns and update its instructions over time.

## Task

- Add Phase 5: MCP tools for the cycle agent, gated by a turn proof (open cycle + matching `campaign.agent_id`).
- Add Phase 6: retrospective logic that fires on terminal/milestone outcomes and writes learnings back into instructions without requiring human approval.
- Add indexes on `campaign_attempts.execution_id` and `eval_job_id` (migration 097).
- Fix: `extend` must refuse an archived room; after-commit hooks must not fire on `SAVEPOINT` commits; cycle Tasks failed by the campaign and campaign/attempt changes must be published after the real commit via `campaigns.notify`.

## Action

**Phase 5 — MCP tools** (`packages/cluster/anygarden/mcp/campaign_tools.py`, `campaigns/agent_actions.py`):
- `record_campaign_attempt`: one call per cycle; refused if evaluation has started; returns follow-up directives (edits, pause, extend) issued since the cycle opened. A scored cycle without a record closes as `no_record` (score kept but never best/target).
- `schedule_campaign_wakeup`: accepts 60 s – 7 d, must be before the deadline; last call wins; next cycle opens at `max(close + 300 s, wake_at)`.
- `report_campaign_blocked`: recorded on the cycle, applied at close after target/budget/evaluator verdicts.
- `read_campaign_attempts`: returns attempts newest-first, limit ≤ 20.
- `packages/cluster/anygarden/mcp/router.py`: wired new tools into the router.

**Phase 6 — retrospectives** (`campaigns/retrospective.py`):
- Triggered on `achieved`, `deadline_reached`, `budget_exhausted` (not `stopped`), and every 10 cycles.
- Runs on the retro reserve with its own brief.
- `apply_campaign_learning` writes through `write_instructions(source="campaign", context=...)` without approval, scoped to `## Campaign learnings` or skills the campaign created; ≤ 3 changes of ≤ 16 KB each.
- Records a campaign event and posts a room message; a failed write (e.g. offline disk machine) is recorded and the retrospective continues.
- Ending sets a default final attempt.

**Supporting changes**:
- `packages/cluster/anygarden/db/migrations/versions/097_campaigns.py`: indexes on `campaign_attempts.execution_id` and `eval_job_id`.
- `packages/cluster/anygarden/campaigns/notify.py` (new): publishes campaign/attempt changes after the real commit; cycle Tasks failed by the campaign trigger publish.
- `packages/cluster/anygarden/campaigns/lifecycle.py`, `scheduler.py`, `service.py`, `evaluation.py`, `brief.py`: `extend` refuses archived rooms; after-commit hooks no longer fire on `SAVEPOINT` commits.
- `campaigns/views.py` (new): HTTP views for campaigns.
- `api/v1/agents.py`, `ws/protocol.py`, `db/models.py`, `instructions/service.py`: wired retrospective and notify hooks.
- Tests: `test_campaigns_cycles.py`, `test_campaigns_execution.py` updated; `test_campaigns_notify.py`, `test_campaigns_retrospective.py`, `test_campaigns_tools.py` added.

## Decisions

- **Turn proof for MCP tools**: requiring the turn to work on the campaign's open cycle (Task or execution) and verifying `caller == campaign.agent_id` prevents rogue agents or stale turns from recording spurious attempts. An alternative (API key–only auth) was weaker because it wouldn't catch cross-campaign identity confusion.
- **`no_record` close state instead of rejection**: a scored cycle that never called `record_campaign_attempt` is closed as `no_record` rather than voided, preserving the score for auditing while ensuring it can never become the best or target result. Voiding it would discard potentially useful evaluation data.
- **Retrospective scope limited to `## Campaign learnings`**: allowing the retrospective to write anywhere in instructions risked overwriting human-authored rules. Scoping to a dedicated section and campaign-created skills contains the blast radius; 3 changes × 16 KB is a generous but bounded budget that prevents runaway rewrites.
- **After-commit hooks not firing on SAVEPOINTs**: firing notify/publish hooks on nested savepoints caused duplicate events when the outer transaction eventually committed. Filtering to real `COMMIT` statements eliminates this class of double-publish bugs. (Assumption: the ORM always wraps nested work in `SAVEPOINT`; if a future migration uses explicit nested transactions differently, this rule needs revisiting.)
- **`extend` refuses archived rooms**: extending a campaign into an archived room would produce cycles with nowhere to post. Fail-fast at `extend` time surfaces the problem immediately rather than letting cycles start and fail silently.

## Result

- Cycle agents can now call four MCP tools to record attempts, schedule wakeups, report blockages, and read history — all within a single open cycle, with appropriate guards.
- Campaign retrospectives run automatically on terminal outcomes and milestone cycle counts, writing learnings back to instructions without human approval.
- Indexes on `execution_id` and `eval_job_id` speed up cycle look-ups during evaluation.
- Campaign/attempt state changes are reliably published via `campaigns.notify` after the real DB commit, with no spurious events from savepoints.
- 5 new test files cover the tools, notify, and retrospective paths; 2 existing test files updated.

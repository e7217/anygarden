# feat(goals): keep earlier no-gain hypotheses in the cycle brief (#953)

- Commit: `a567f8d` (a567f8d99189dc19cef4501cbfa14b741616991c)
- Author: Changyong Um
- Date: 2026-10-28T10:56:14+09:00
- PR: — (issue #953)

## Situation

The cycle brief surfaced only the five most recent campaign cycles to the agent. Cycles that aged out of that window were effectively invisible, so the agent could re-propose hypotheses that the evaluator had already judged `no_gain` or `regressed`, believing them untried. This created redundant work and slowed convergence on genuinely novel ideas.

## Task

- Extend the failure-patterns section of the cycle brief to surface hypotheses from older cycles that received a no-gain verdict (`no_gain`, `regressed`, `guardrail_violation`, `invalid`).
- Limit the list to at most 30 entries, newest first, each clipped to 160 characters.
- Exclude cycles where the evaluator itself failed (`eval_failed`) or the result was never recorded (`no_record`), since those do not reflect judgment of the idea.
- Add overflow hint pointing to `read_campaign_attempts` when the cap is hit.
- Add requirement CMP-15.

## Action

- `docs/requirements.md` line 163: Added CMP-15 row specifying the behaviour and test coverage marker.
- `packages/cluster/anygarden/campaigns/brief.py`:
  - Added constants `OLDER_FAILED_MAX = 30`, `_OLDER_FAILED_FIELD_MAX = 160`, and `_NO_GAIN_OUTCOMES` frozenset.
  - Updated module docstring to mention the new section.
  - In `_patterns()`: after the recent-lessons block, filters `closed[:-RECENT_ATTEMPTS]` for attempts whose `outcome in _NO_GAIN_OUTCOMES` and that have a `hypothesis`. Lists them newest-first (`reversed(older[-OLDER_FAILED_MAX:])`), with a prose header instructing the agent not to repeat unchanged ideas and to explain what changed if retrying. Appends an overflow line when `len(older) > OLDER_FAILED_MAX`.
- `packages/cluster/tests/test_campaigns_brief.py`:
  - `test_older_cycles_without_gain_stay_visible`: verifies hypotheses from cycles 1 and 3 (no_gain/regressed) appear; cycle 2 (improved) and recent cycles do not; newest-first order is enforced.
  - `test_evaluation_failures_are_not_listed_as_failed_ideas`: verifies `eval_failed` and `no_record` cycles are excluded even when all recent cycles are improvements.
  - `test_older_failed_ideas_are_capped`: builds 59 cycles, confirms the 30-entry cap, the cut-off point (cycle 24 excluded, 25 included), and the overflow announcement.

## Decisions

- **Server-built list (A) over agent-written notes (B) or mid-turn notices (C)**, borrowed from microsoft/Agensh's shared `FAIL` board. Agensh makes agents write `FAIL` because nothing else judges an idea; a goal already has the evaluator's outcome, and the brief was simply dropping it after five cycles. B (typed notes tool + table) was deferred: with one lead agent running cycles serially its unique value narrows to structured notes and search, in-cycle findings already have `lessons`, and its effect depends on agents writing notes during real work, which offline tests cannot measure — revisit if goals become multi-agent. C was rejected: it conflicts with the #624 attention norm and has no second agent to notify. (`.tmp/plan-953-goal-tried-without-gain.md`)
- **Offline A/B before merging** (synthetic 14-cycle histories, 10 proposals per arm, LLM judge checked by hand): when the next natural idea collided with an invisible old failure, the current brief led a small model to re-propose it as "untried" 20/20 (larger model 6/20) and the list cut that to 0/40 (Fisher p 2e-13 / 8e-4). In a scenario where the old failure had become promising (bottleneck moved), the list did not suppress the retry — 19/20 retries now cited the earlier cycle and what changed. A control scenario with no collision showed no difference. Cost ≈ 700 chars (~200 tokens) per brief. Not measured: effect on real cycle scores.
- **Heading wording v2** ("scored by the evaluator on the code of that cycle … a retry is fine when the code or setup has changed") over v1 ("unless your hypothesis says what is different"): not statistically different in the A/B, chosen because it states why the old score may no longer apply.
- **Kept inside `## Failure patterns`** rather than a new section, so `SECTION_TITLES` (index-based tests) stays unchanged; not merged with the retrospective's "did not help" list, whose selection purpose differs.
- **Scope to evaluator verdicts only, not execution failures**: `eval_failed` and `no_record` indicate infrastructure or tooling issues, not a judgment that the idea is ineffective. Including them would mislead the agent. The `_NO_GAIN_OUTCOMES` set makes this boundary explicit and easy to update.
- **Newest-first order**: the agent is most likely to re-propose ideas that were tried recently; placing them first in the list makes the warning most salient for the likeliest collision, without requiring the agent to read the whole list.
- **160-char clip and 30-line cap**: the brief has a hard `BRIEF_MAX_CHARS` ceiling and the new section must not crowd out recent-cycle data. 160 chars is enough to identify a hypothesis; 30 lines covers realistic campaign depths without dominating the brief. Overflow is announced rather than silently dropped so the agent knows to call `read_campaign_attempts` for the full picture.
- **Opt-in retry framing**: the heading explicitly allows a retry if the code or setup has changed and asks the agent to say what changed. This avoids a hard block on previously rejected ideas while providing a quality gate against mindless repetition.

## Result

The cycle brief now surfaces up to 30 earlier no-gain hypotheses in the failure-patterns section, reducing re-proposal of already-judged ideas. Three new `@pytest.mark.req("CMP-15")` tests cover the inclusion logic, the exclusion of evaluation failures, and the 30-entry cap. Requirement CMP-15 is recorded in `docs/requirements.md`.

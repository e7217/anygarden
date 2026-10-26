# fix(campaigns): say that a wake-up schedules the next cycle, not a wait inside this one (#930)

- Commit: `72f6ee6` (72f6ee6)
- Author: Claude Opus 5.5
- Date: 2026-10-26
- PR: #930

## Situation

The `schedule_campaign_wakeup` tool description was ambiguous about its scope.
Agents could misread it as a general-purpose pause mechanism to wait for their
own child task results within a cycle, rather than a scheduling hint that
applies only to the *next* cycle's start time. This risked misuse: an agent
ending a turn and expecting `schedule_campaign_wakeup` to resume it on a
sub-task result, when the correct pattern is simply to end the turn and let the
server wake the agent when the child task finishes.

## Task

- Clarify the `schedule_campaign_wakeup` tool description in `campaign_tools.py`
  so agents understand it defers the **next** cycle's opening, not the current one.
- Make explicit that the tool is not a wait-for-child-task mechanism.
- Mirror the same clarification in `docs/campaigns.md` (Korean user guide), in
  the tool table at the 에이전트가 쓰는 도구 section.

## Action

- `packages/cluster/anygarden/mcp/campaign_tools.py` lines 43–57:
  Updated the docstring of `schedule_campaign_wakeup` to add the sentence
  "It does not pause or extend the current cycle and is not a way to wait for
  your own child task (end the turn; the server resumes you when its result
  arrives)." The delay window and deadline semantics were unchanged.
- `docs/campaigns.md` line 80:
  Updated the Korean description of `schedule_campaign_wakeup` in the tool
  reference table to read "이번 사이클이 끝난 뒤 **다음** 사이클을 특정 시각
  이후로 미룸… 이번 사이클 안에서 자기 하위 작업을 기다리는 용도가 아님
  (턴을 끝내면 서버가 결과 도착 시 다시 깨움)".

## Decisions

- **Prose fix only, no schema change**: the tool's JSON schema (required fields,
  types, constraints) was correct; only the natural-language description misled
  agents. Changing the schema was not needed and would have bumped the MCP
  contract unnecessarily.
- **Korean doc updated alongside the Python tool**: the docs and the tool
  description must agree; a discrepancy between them would confuse both human
  readers of the guide and agents that are pointed at the guide as reference.
  Keeping both in sync was the lowest-risk, highest-clarity option.
- **No runtime guard added**: adding a server-side rejection of wakeup calls
  made from inside a cycle (unclear semantic anyway) was considered but
  rejected — the clarification in the description is sufficient, and a runtime
  guard would require schema/flow changes outside this PR's scope.
- Assumption: agents read tool descriptions carefully. If future evaluation
  shows agents still misuse the tool, a runtime guard or a dedicated
  "wait_for_child" return value from `complete_project_execution` should be
  revisited.

## Result

- `schedule_campaign_wakeup` description now unambiguously scopes the tool to
  next-cycle scheduling and explicitly tells agents not to use it as an
  intra-cycle wait.
- Korean user guide (docs/campaigns.md) reflects the same constraint.
- No behaviour change at runtime; existing callers using the tool correctly are
  unaffected.
- No tests were added (description-only change); the existing test suite
  remains green.

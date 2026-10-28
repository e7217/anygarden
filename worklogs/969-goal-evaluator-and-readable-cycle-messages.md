# fix(goals): make evaluators count what the metric says and cycle messages readable (#969)

- Author: Claude (assisted, user-directed)
- Date: 2026-10-28
- Issue: #969 (items 3a and 6)

## Situation

In the reviewed goal run the metric was "deduplicated ideas with peer feedback and PM
response". The evaluator scored 30, because it counted an idea as a duplicate only when
the normalized title or problem/solution text was identical. The lead had recorded
meaning-level merges (`merge_into`, `semantic_counted`) in `decisions.json`, but the
script never read that file, and the evaluator is pinned during cycles. By the lead's
own count the result was 24, yet a person confirmed the goal as achieved.
Separately, the cycle completion message read like an internal log ("시도기록1회 완료,
follow_up 없음") and the retrospective message defended the rules.

## Task

Change the guidance so that a goal draft's evaluator measures what its metric name
promises, and so that cycle and retrospective messages are written for people.

## Action

- `workspace/drafts._goal_brief` step 2: the script counts exactly what the metric name
  says. A part that needs judgement is recorded in a file field during the work and the
  script reads that field; otherwise the metric is named after what the script really
  counts.
- `propose_goal` schema: `metric.name` "Says exactly what the evaluator counts".
- `campaigns/brief.py`: a shared `_READABLE_REPLY` rule closes both the cycle rules (8)
  and the retrospective rules (6). It asks for complete sentences in the room's
  language, no tool names or flags, and no defending of the rules.
- Added requirement CMP-19.

## Result

- 2 new tests: `test_cycle_and_retro_rules_ask_for_a_readable_final_message` and
  `test_goal_guidance_makes_the_evaluator_count_what_the_metric_says`. Both failed
  before the change.
- Campaign, workspace, goal and MCP suites: 664 passed.
- These are prompt changes. Whether models follow them can only be seen in live runs.
  Item 3b (show the lead's confirm recommendation next to the confirm button) is the
  next PR.

# docs(campaigns): note that a cycle needs a sealed child task to complete (#930)

- Commit: `3a899a8` (3a899a8)
- Author: Claude Opus 5.5 (Co-Authored-By)
- Date: 2026-10-26
- PR: #930

## Situation

The campaign user guide (`docs/campaigns.md`) described the agent tools and the general flow for cycle execution, but it did not explain a concrete constraint of `complete_project_execution`: the call fails unless at least one child task has been sealed. Without this note, a campaign room operator might set up a room with only a representative agent and no sub-rooms, then wonder why cycles never finish.

## Task

Add a paragraph to the "에이전트가 쓰는 도구" section — immediately after the existing sentence about cycles ending without a record — that explains:

- `complete_project_execution` requires ≥1 sealed child task to succeed.
- A campaign room therefore needs sub-rooms each with an assigned agent.
- The representative agent must delegate at least one task per cycle and seal the plan.
- If no sub-room exists, the cycle cannot end and the agent must call `report_campaign_blocked`.

No API or behaviour changes; documentation only.

## Action

`docs/campaigns.md` lines 86–87 (after "기록 없이 끝난 사이클은 `기록 없음`으로 남습니다."):

Added paragraph:

> 현재 `complete_project_execution`은 확정(seal)된 하위 작업이 하나 이상 있어야 실행을 끝냅니다. 따라서 캠페인 방에는 하위 방과 그 방의 담당 에이전트가 있어야 하고, 대표 에이전트는 사이클마다 하위 작업을 하나 이상 맡긴 뒤 계획을 확정해야 합니다. 하위 방이 없으면 사이클이 끝나지 못해 에이전트가 `report_campaign_blocked`로 알리게 됩니다.

One file changed, one paragraph inserted.

## Decisions

- **Inline note vs. separate section**: A separate "알려진 제약" section was considered, but the constraint is tightly coupled to the `complete_project_execution` usage pattern already described in the tools table. Placing it inline directly after the "no-record" sentence keeps the flow and avoids fragmenting the section.
- **Wording "현재" (currently)**: The word signals that this is an implementation-level constraint that may be relaxed in a future issue, rather than a permanent design principle. This avoids confusion if the constraint is later lifted.
- **`report_campaign_blocked` as the recommended action**: The guide already documents this tool; referencing it here closes the loop — the reader learns both what happens and what the agent should do about it.
- Assumption: if `complete_project_execution` is changed to allow zero sealed child tasks (e.g. for solo-agent campaigns), this paragraph must be revisited.

## Result

Users reading the campaign guide now understand that a campaign room must have sub-rooms before cycles can complete. Agents encountering the constraint have a documented recovery path (`report_campaign_blocked`). No tests were added (documentation change). No behaviour changed.

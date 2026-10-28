# feat(goals): report goal results in the main chat and attach deliverables (#964)

- Commit: `5534aaa` (5534aaa746f7f67687e49457a04babf6f21ca384)
- Author: Changyong Um
- Date: 2026-10-28T15:34:29+09:00
- PR: #964

## Situation

Goal cycles run in Task threads, meaning status changes (achieved, blocked, confirmation needed) happen server-side with no agent turn to narrate the result in the main chat. The status-change notification added in #957 posted a card but mentioned nobody and attached no files, so the person had to hunt the cycle threads to understand what happened. Deliverable files existed in `RoomArtifact` rows but were never surfaced alongside the status card. `memory/outbox/…` links written by agents in cycle replies appeared as dead relative URLs in the chat UI.

## Task

- Open a **report turn** for the goal's lead agent at four moments: awaiting confirmation, goal ended (achieved/deadline/budget), blocked/plateau (needs_person), and every `RETRO_EVERY_CYCLES` closed cycles while still active.
- The report request must be a single top-level system message that (a) keeps the `goal_status` card, (b) mentions the lead, (c) carries cycle deliverables as `artifact` references, and (d) contains the lead's brief as the body (not shown to people, same as `/goal` draft cards).
- Attach the cycle's deliverable files (`RoomArtifact` rows) to the cycle-completion message too.
- Fix `memory/outbox/<room>/<name>` hrefs in the frontend to resolve to the same-named attached artifact instead of navigating to a broken path.
- Update requirements: revise CMP-16 (report statuses now mention the lead), add CMP-17 (report turn) and CMP-18 (artifact attachment and link conversion).
- The call must be idempotent per `state_revision` so a retry or duplicate transition does not open a second turn.

## Action

**New `packages/cluster/anygarden/campaigns/report.py`**
- `cycle_artifacts(db, campaign, attempt)` — queries `RoomArtifact` rows for the lead in the campaign's room during the attempt's time window (from `opened_at` to the next attempt's `opened_at` or now).
- `report_artifacts(db, campaign)` — collects files of the best-scored and the latest cycle, deduplicates, caps at `MAX_REPORT_ARTIFACTS = 10` (split evenly when both cycles contribute).
- `artifact_reference(row)` — builds the canonical `artifact` reference dict (`messages/references.py` format).
- `render_report_request(campaign, *, reason, lead_participant_id, best, latest, files, detail)` — returns the full brief string: goal status headline, five-point writing instruction (result line, deliverable summary, what worked, next action, outbox instruction), cycle context (score, hypothesis, change summary), and attached file list.
- `request_report(db, campaign, *, reason, previous, detail)` — idempotency check via `AgentTurn.idempotency_key == report_key(campaign, reason)`; assembles metadata (`goal_status`, `system_origin`, `goal_report`, `mentions`, `references`); calls `append_message` then `create_turn` for the lead's turn; returns `None` when already reported or when the room rejects direct messages (`ChannelError`).
- `report_reason(new_status, *, event)` — maps status → reason; returns `None` for `achieved` via `confirm` event (lead already reported before the person confirmed) and for `stopped`.

**`packages/cluster/anygarden/campaigns/service.py`** (`apply_transition`)
- For statuses that map to a report reason (`_STATUS_REASON`), call `request_report` instead of `_post_status_notice`; a person's `confirm` or `stopped` events keep the plain notice path.

**`packages/cluster/anygarden/campaigns/lifecycle.py`**
- After closing a work cycle: if `cycles_run % RETRO_EVERY_CYCLES == 0` and the campaign status is still `active`, call `request_report(reason="progress")`.

**`packages/cluster/anygarden/project_executions/service.py`** (`complete_project_execution`)
- If the root Task has a `campaign_id`, call `cycle_artifacts` for the closing attempt and embed the references into the cycle-completion message metadata.

**`packages/cluster/anygarden/campaigns/brief.py`** (`_rules`)
- Added one bullet asking the lead to save deliverables as outbox files (`memory/outbox/<room_id>/`) so the server can find and attach them.

**`packages/cluster/frontend/src/components/MarkdownContent.tsx`**
- Added optional `references` prop (list of artifact references).
- `memory/outbox/<room>/<name>` hrefs are matched against references by filename; matched links open `/api/v1/rooms/<room>/artifacts/<id>`; unmatched links render as plain text to avoid navigating to dead paths.

**`packages/cluster/frontend/src/components/MessageBubble.tsx`** (lines ~571, 605, 655)
- Passes `message.metadata.references` down to `MarkdownContent`.

**`packages/cluster/frontend/src/components/GoalStatusCard.tsx`** and **`lib/goalStatus.ts`**
- Report request messages carry `goal_status` metadata and are rendered by the existing status card component; added display of who will report and the attached file list.

**`docs/requirements.md`**
- CMP-16 updated: report-status transitions mention the lead (no longer silent).
- CMP-17 added: the server opens a lead turn for report requests.
- CMP-18 added: cycle deliverables are attached; `memory/outbox` links resolve to attached files.

## Decisions

**Decision 1 — how to open the agent's turn**
Three alternatives were weighed (plan §3.2, decision 1):
- *A. Top-level system message + `create_turn`* (the `/goal` draft pattern from `workspace/drafts.py:208–221`) — already-proven path, keeps the reply in main chat, costs no goal budget. **Chosen.**
- *B. Create a report Task/execution* — ties the report to the goal budget, and the reply ends up in a thread, reproducing the original problem.
- *C. Add a brief rule asking the lead to self-report* — cheapest to implement, but the agent can omit it, the reply goes into a thread, and status transitions (e.g. `awaiting_confirmation`) happen with no running agent turn at all.
- Deciding factor: status transitions are purely server-side events; only the server can guarantee the turn is opened.

**Decision 2 — merge status notice and report request into one message**
Two-message approach (notice + separate report request) was rejected because it adds noise (plan §3.2, decision 2). For report statuses, the status card is the report request: same message, same `goal_status` block, now with lead mention and artifact references. Non-report transitions (if any remain) keep the original silent notice.
- CMP-16's "never mentions anyone" clause is intentionally narrowed.
- `stopped` via a person's explicit action is excluded from reporting (they stopped it themselves); `room_archived` stop is also excluded.

**Decision 3 — where to convert `memory/outbox` links**
- *Server-side rewrite at storage time*: mutates the original body; past messages stay broken.
- *Frontend `MarkdownContent` matching by name against the message's `references`*: **chosen** — original content is preserved, past messages retroactively rendered correctly once they carry references.
- Unmatched links render as text (not a broken anchor) to prevent accidental navigation.

**Decision 4 — progress report interval**
Reused the existing `RETRO_EVERY_CYCLES` constant rather than introducing a separate one (plan §3.2, decision 4). The retrospective (budget-gated) remains an internal cycle event; the progress report is a separate agent turn.

**Assumptions to revisit:**
- If the lead is not in the room, `create_turn` is skipped but the message (status card + files) still posts — acceptable degradation.
- The window ends at the next attempt's `opened_at` (not `closed_at`) because outbox files reach the server with the turn's reply, after the run finished.
- Artifact–cycle matching relies on `created_at` timestamps. If the lead uploads unrelated files during a cycle they will be included; the three-way filter (lead, room, time window) was judged sufficient.

## Result

- Goals now post a top-level report request in the main chat at the four defined moments; the lead agent's reply (explanation, deliverable summary, next-action guidance) appears as a normal top-level message visible to all room members.
- Cycle-completion messages carry the cycle's deliverable files as artifact references.
- `memory/outbox/…` links in the lead's replies resolve to the named attached file; unmatched links render as text rather than broken anchors.
- Requirements CMP-16, CMP-17, and CMP-18 are up to date.
- Idempotency key (`goal-report:<id>:<reason>:<state_revision>`) prevents duplicate report turns on retried transitions.

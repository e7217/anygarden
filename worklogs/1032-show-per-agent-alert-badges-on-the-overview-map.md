# feat(agent-links): show per-agent alert badges on the overview map (#1032)

- Commit: `07c07a8` (07c07a8df701e9a39447bb82183b6c62454eb901)
- Author: Changyong Um
- Date: 2026-11-12T17:48:32+09:00
- PR: #1032 (issue)

## Situation

The Overview map (#1023, redrawn as engine-logo circles in #1031) showed what each agent is doing, but not what needs the person. Failed tasks, requests waiting for approval, finished answers left unread, and queued work were spread across the inbox, rooms and task rails. Nothing tied them to a specific agent. The data existed, but no endpoint returned it per agent and limited to the rooms a person can open.

## Task

- Put alerts on each agent circle. The top right shows one badge in priority order: problem, then waiting on you, then unread finished answer. The bottom right shows a queued-task count, with no colour change.
- Each alert clears when its cause does. No "acknowledged" state is stored and no migration is needed.
- Respect room visibility. Agents that cannot answer get no badges. Shapes differ (`!`, clock, dot, number), so colour is not the only signal.

## Action

- `packages/cluster/anygarden/orchestration/agent_alerts.py` (new): `agent_alerts(db, room_ids, user_id, inbox_items, now)` returns `agent_id -> alerts`:
  - failed and queued tasks: non-silent, assignee → `Participant.agent_id`
  - failed rooms: a `row_number()` window over completed and failed turns per (agent, room), where the latest is `failed`. Cancelled turns are ignored.
  - waiting: pending, unexpired `WorkspaceActionRequest`s, plus inbox approval and question items with `needs_action`, keyed by `requester_agent_id`
  - unread rooms: answers from completed, non-skipped turns with `seq` above the user's `last_read_message_seq`
  - lists capped at 5, with `*_count` fields
- `GET /api/v1/agent-links/alerts` in `packages/cluster/anygarden/api/v1/agent_links.py`: people only. It uses `accessible_room_ids(scope="rooms.collection", include_shared=True)`, the same as `/turns/active`. Project items come from `list_inbox`, so they match the inbox exactly.
- `packages/cluster/tests/test_agent_alerts.py`: one test per alert covering it appearing and clearing, silent tasks, room visibility, and API permission.
- Frontend:
  - `lib/agentLinks.ts`: `AgentAlerts`, `fetchAgentAlerts`, and `topAlert` (priority).
  - `hooks/useAgentAlerts.ts`: 15s poll while the page is visible.
  - `AgentLinksPage.tsx` passes `alerts` and `onOpenHref` to the canvas.
  - `AgentLinksCanvas.tsx` renders the badges, adds card lines that open the room or inbox item, and adds an alert summary to the node's aria-label.
  - CSS places the badges at 45° on the circle, clear of the side ports. i18n keys are added for en and ko.
- Tests: vitest for `topAlert`, the fetch, the hook and the page wiring. An e2e `[COL-20]` test covers badge priority, the queued count, no badges on unavailable agents, and the card lines. COL-20 is added to `docs/requirements.md`, and `docs/agent-links.md` is updated.

## Decisions

- **Delivery:** a separate `/agent-links/alerts` endpoint, instead of new fields on `GET /agent-links` or combining existing APIs in the browser.
  - The graph endpoint has no room filter and is only refetched on link edits.
  - Three of the four alerts had no user-scoped API at all, so a server aggregate was needed anyway.
- **Problem clearing:** clears automatically when the cause is resolved. The user chose this over storing "acknowledged", which would have required a new table and a migration.
- **Waiting-on-you style:** a teal clock badge with priority below a problem. The user found approvals shown in red awkward, because waiting is not a failure.
- **Waiting scope:** workspace actions, project approvals and project questions. General-room `TaskInputRequest` is left for later; it has no list API and a different lifecycle.
- **Unread:** only answers from completed turns count, not every message from the agent, so progress chatter does not light the dot. It uses the room cursor, the same as the sidebar dot.
- **Queued count:** no colour threshold, at the user's request.
- **Assumptions:**
  - Rooms in `rooms.collection` cover task rooms well enough. Execution rooms that only operating-room readers can see may be missed.
  - A 15s poll with five queries is cheap enough. Revisit with an index on `messages(room_id, participant_id)` or a cache if it is slow.

## Result

- Agents show one priority badge and a queued count. Hovering or selecting an agent lists each item with a link, and every alert clears with its cause.
- Tests passed:
  - pytest `test_agent_alerts.py` + `test_agent_links.py`: 28
  - vitest: page, lib, hook and agent-links components
  - e2e `agent-links.spec.ts`: 21
  - `npm run build`
- Pending: live verification on a real node; `TaskInputRequest` in the waiting badge; performance measurement on a large DB.

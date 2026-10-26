# feat(campaigns): manage campaigns over REST and push their changes to the room (#930)

- Commit: `89a4ca2` (89a4ca2)
- Author: Claude Opus 5.5
- Date: 2026-10-26
- PR: #930

## Situation

The campaign domain (Phase 1–6) had its scheduler, service layer, cycle execution hooks, retrospectives, and MCP tools in place, but no REST surface. Operators and users could not create, inspect, pause, extend, or otherwise manage campaigns through the API. WS notifications for campaign state changes were also missing from the REST layer, so room subscribers would not learn about campaign mutations that originated from HTTP calls.

## Task

- Implement `POST/GET /api/v1/rooms/{room_id}/campaigns` and `GET/PATCH /api/v1/campaigns/{id}`.
- Add action endpoints: `pause`, `resume`, `stop`, `extend`, `confirm`, `reject`, `select-final`.
- Add `GET .../attempts` and `GET .../events` listing endpoints with pagination.
- Add `POST .../attempts/{attempt_id}/trusted-score` to record externally verified scores.
- Apply granular permissions: read = room read access; create = task management in room; mutate = campaign owner or global admin; set evaluator = machine owner or admin.
- Map `CampaignPolicyError`, `IllegalTransition`, and `CampaignConflict` onto the v1 error envelope (`CAMPAIGN_*` codes, `reason` = policy rule string).
- Broadcast `campaign.updated` and `campaign.attempt_updated` WebSocket events to the room after every mutation.
- Stamp edits and status actions that land while a cycle is open with `open_cycle_no` so `record_campaign_attempt` can return them as follow-up directives to the running agent.

## Action

- **`anygarden/api/v1/campaigns.py`** (new file, ~640 lines): FastAPI `APIRouter` with all 12 endpoints listed above.
  - Pydantic input schemas (`CampaignCreate`, `CampaignUpdate`, `ExtendIn`, `RejectIn`, `SelectFinalIn`, `TrustedScoreIn`) with `extra="forbid"` and finite-float validators.
  - Output schemas (`CampaignOut`, `CampaignAttemptOut`, `CampaignEventOut`, `AttemptPage`, `EventPage`, `ScoreRef`, `OpenAttemptRef`).
  - `_load` / `_load_owned` helpers enforce room-read and owner-or-admin checks.
  - `_can_set_evaluator` checks the campaign's agent placement to verify machine ownership before allowing evaluator changes.
  - `_run` dispatches to `campaigns.actions.*` and maps exceptions to v1 error responses.
  - `_respond` calls `notify.mark` + `notify.commit_and_publish` then refreshes the campaign view before returning.
- **`anygarden/campaigns/actions.py`** (new/extended): business logic for `edit_campaign`, `pause_campaign`, `resume_campaign`, `confirm_campaign`, `reject_campaign`, `select_final`, `record_trusted_score`, and `open_cycle_no`.
  - `edit_campaign` validates the full spec via `validate_campaign_spec`, diffs only changed fields, runs a CAS update, and records an `edited` event with `open_cycle_no`.
  - `resume_campaign` resets `consecutive_eval_failures` when leaving `blocked` and `cycles_since_improvement` when leaving `plateau`.
  - `record_trusted_score` transitions to `achieved` via `trusted_target_reached` if the score meets the target in target mode.
  - `select_final` validates the attempt is a closed cycle with a valid score and stores `final_attempt_id`.
- **`anygarden/campaigns/notify.py`** (new file, ~186 lines): session-scoped WS mark-and-publish mechanism. `mark()` tags campaign ids, attempt ids, and task ids on the SQLAlchemy session's `info` dict. `commit_and_publish` / `publish` flush marked ids to `CampaignUpdatedOut` / `CampaignAttemptUpdatedOut` broadcasts. A SQLAlchemy `after_commit` event listener publishes any remaining marks in a background task (for code paths that commit outside the REST router). An `after_rollback` listener drops marks on rollback.
- **Tests** added in `tests/test_campaigns_api.py`, `tests/test_campaigns_tools.py`, `tests/test_campaigns_retrospective.py`, `tests/test_campaigns_notify.py`.

## Decisions

From `.tmp/plan-930-metric-campaigns.md` §3.8 and the commit body:

- **Thin router over a `campaigns/actions.py` layer**: The router owns only HTTP concerns (request parsing, permission checks, error mapping). Business logic lives in `actions.py` so MCP tools and the scheduler can reuse it without going through HTTP. Alternative — inline logic in the router — was rejected as it would duplicate the mutation semantics for MCP.
- **v1 error envelope for all campaign errors**: `CAMPAIGN_*` codes with a `reason` field carrying the policy rule name (e.g. `TARGET_REQUIRED`) let clients distinguish actionable input errors from conflict/state errors without parsing free-form messages. Alternative — generic 4xx with free-form message — was rejected because it makes client-side UX strings hard to localize.
- **`open_cycle_no` stamping on every mutation event**: Any edit or status action that arrives while a cycle is open carries the cycle number in the event's `details`. `record_campaign_attempt` returns these events as follow-up directives, so the running agent's next turn sees the change without a poll. Alternative — agent polls on each turn — was rejected because it adds latency and couples the cycle to the scheduler clock.
- **Session-level WS mark/publish instead of immediate broadcast**: The campaign hooks run inside other transactions (sweeper, machine evaluator, room archive). Broadcasting inside a transaction risks emitting an event that then rolls back. The `mark` / `after_commit` listener pattern ensures notifications are only sent after a successful commit. (Unclear from available context: whether the background publisher's error handling is tested under concurrent rollbacks.)
- **Evaluator permission tied to machine ownership**: The evaluator command runs on the agent's machine; giving it to anyone who owns the campaign would let a user inject arbitrary shell commands on a machine they do not control. Restricting it to the machine's owner or a global admin is a security boundary that matches the existing `Machine.owner_user_id` model.

## Result

All 12 REST endpoints are live. Mutations commit and publish `campaign.updated` / `campaign.attempt_updated` to the room over WebSocket. Ruff reports "All checks passed" against the new files and their tests. The feature branch now covers the full Phase 7 scope (REST management layer). Open-cycle follow-up directives will be delivered when `record_campaign_attempt` is next called by the scheduler.

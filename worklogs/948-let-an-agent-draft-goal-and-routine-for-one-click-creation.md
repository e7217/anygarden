# feat(chat): let an agent draft /goal and /routine for one-click creation (#947, #948)

- Commit: `31089dd` (31089ddb90b6f50949a6f6534494933e3582a25b)
- Author: Changyong Um
- Date: 2026-10-27T16:17:03+09:00
- PR: #948

## Situation

Goals and routines had to be created entirely by hand via the workspace form dialogs. There was no way for a person to say "draft me a goal to measure X" in chat and have an agent prepare a ready-to-approve card. The approval card UI from #938 existed but was only used for workspace_action requests; the `/goal` and `/routine` slash commands only opened the empty forms.

## Task

- Add `/goal <description>` and `/routine [@agent] <description>` slash commands that trigger an agent to produce a goal or routine draft.
- Record a `DraftRequest` in the DB, start the drafter's agent turn with a structured brief, and limit `propose_goal`/`propose_routine` tool calls to that one turn (turn proof).
- Validate the draft server-side against the same rules as the create routes (campaign policy, lead, trigger config, assignee in the report room, active-routine cap).
- Present a valid draft as a #938 approval card with a readable summary and Create / Edit-in-form / Reject actions.
- Accept edited goal and routine params at approval time; goal approval requires the evaluator before the request is claimed.
- Default cron drafts to the requester's browser timezone when no timezone is supplied.
- If the turn ends without a proposal, post a notice with an "Open an empty form" button.
- Add DB migration 101 (`workspace_draft_requests`) and WSP-06..08 spec coverage.

## Action

- **`packages/cluster/anygarden/workspace/drafts.py`** (new, 415 lines): core orchestration — `resolve_drafter` picks who drafts based on kind and room membership; `open_draft` records `DraftRequest`, posts a structured notice with REQUIRED ACTIONS, and starts the drafter's turn; `close_unanswered_drafts` handles expired draft turns; `submit_goal_draft` / `submit_routine_draft` validate and convert a passing draft into a `WorkspaceActionRequest`.
- **`packages/cluster/anygarden/mcp/draft_tools.py`** (new, 124 lines): defines `DRAFT_TOOL_SCHEMAS` for `propose_goal` and `propose_routine`, wired with `turn_proof` so the tools only work inside the draft turn; `call_draft_tool` dispatches to the appropriate submit function.
- **`packages/cluster/anygarden/mcp/router.py`**: registers `DRAFT_TOOL_NAMES` alongside other workspace tool lists so the tools appear in every agent's MCP tool listing.
- **`packages/cluster/anygarden/workspace/approvals.py`** (+77 lines): approval handler now accepts edited goal/routine params; goal approval checks the evaluator pre-claim; readable card summary rendered from draft params.
- **`packages/cluster/anygarden/workspace/router.py`** (+71 lines): adds `POST /workspaces/{id}/draft_goal` and `POST /workspaces/{id}/draft_routine` endpoints.
- **`packages/cluster/anygarden/db/workspace_action_models.py`** (+46 lines): `DraftRequest` ORM model with kind, room, requester, drafter, assignee, description, and status fields.
- **`packages/cluster/anygarden/db/versions/101_workspace_draft_requests.py`** (new, 55 lines): Alembic migration creating the `workspace_draft_requests` table.
- **`packages/cluster/frontend/src/components/drafts/DraftParts.tsx`** (new, 240 lines): `DraftCard` component rendering the readable draft summary; `DraftActionButtons` for Create / Edit / Reject.
- **`packages/cluster/frontend/src/lib/drafts.ts`** (new, 117 lines): client-side helpers for draft submission API calls and draft state types.
- **`packages/cluster/frontend/src/lib/slashCommands.ts`** (+25 lines): `/goal` and `/routine` slash command handlers; both open empty forms when invoked without text, or post to the draft endpoint when text is given.
- **`packages/cluster/frontend/src/components/WorkspaceActionCard.tsx`** (+47 lines): renders `DraftCard` for draft-type workspace action requests alongside the existing approval card.
- **`packages/cluster/frontend/src/pages/ChatPage.tsx`** (+52 lines): connects draft submission flow; passes requester's `Intl.DateTimeFormat().resolvedOptions().timeZone` as `timezone` in draft requests.
- **`packages/cluster/frontend/src/components/campaign/campaignDraft.ts`** (new, 32 lines): campaign-policy helpers reused for client-side draft routing.
- **`packages/cluster/tests/workspace/test_drafts.py`** (new, 275 lines): unit/integration tests covering `resolve_drafter` routing, `open_draft`, turn-proof enforcement, submit validation, and close-unanswered behavior.
- **`docs/workspace-tools.md`** (+23 lines) and **`docs/requirements.md`** (+3 lines): updated spec for WSP-06, WSP-07, WSP-08.

## Decisions

- **Turn-proof via `turn_proof` guard rather than a per-room flag**: reusing the existing `turn_proof` mechanism (checks that the calling agent's current turn was started by this draft request) avoids adding a separate "draft active" lock column and keeps the enforcement local to the MCP tool call handler. The alternative — a boolean on `DraftRequest.active` cleared on commit — would race if two draft turns overlapped.
- **Validation at submit time (not at card creation)**: the draft is checked against the same rules as the create routes only when `propose_goal`/`propose_routine` is called, not when the approval card is clicked. This lets the agent fix errors before the person sees a card, keeping the UX clean. The trade-off is that edge cases (a campaign policy change between draft and approval) could invalidate an already-shown card; those are caught at approval.
- **Approval accepts edited params for goals and routines only**: other workspace action types (one-shot commands) are not editable at approval because their intent is captured in the spec text, not structured fields. Goals and routines have well-defined structured fields that users reasonably want to adjust.
- **Evaluator presence enforced at claim, not draft**: a goal draft can omit the evaluator at first; the agent must provide it before the request is claimed. This mirrors the create route's behavior and avoids blocking a draft that the agent intends to refine via a follow-up workspace write.
- **Browser timezone for cron defaults**: sending `Intl.DateTimeFormat().resolvedOptions().timeZone` from the frontend removes the need for the server to guess or ask; if the field is absent the server defaults to UTC.

## Result

- `/goal <text>` in any campaign room triggers the lead agent to draft a goal and post a Create/Edit/Reject card; `/goal` alone opens the empty form unchanged.
- `/routine [@agent] <text>` triggers the named (or inferred) agent to draft a routine; `/routine` alone opens the empty form unchanged.
- The draft turn is turn-proofed: the MCP tools return an error if called outside the designated turn.
- Server-side validation returns actionable error messages to the agent for retry before the card is shown.
- 275 new test cases cover drafter resolution, validation edge cases, and turn-proof enforcement.
- Migration 101 is included; WSP-06..08 are now covered in the spec.

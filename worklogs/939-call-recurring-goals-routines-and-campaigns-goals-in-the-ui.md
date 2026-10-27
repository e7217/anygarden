# feat(naming): call recurring goals "routines" and campaigns "goals" in the UI (#939)

- Commit: `c6d9bb8` (8a8bb13f168bb08c23aa9b429a766ee45be3014a)
- Author: Changyong Um
- Date: 2026-10-27T12:00:27+09:00
- PR: — (issue #939)

## Situation

The recurring-duty entity (code `Goal`, #302) was shown as "책임 / Responsibilities", a name that says nothing about what it does: run assigned work on a cron or interval. The metric-driven loop (#930, code `Campaign`) was shown as "캠페인", which users found hard to write, while its copy already leaned on "목표" ("목표 캠페인", "목표 {{target}} 이상"). Claude Code and Codex use `/goal` for "loop until a condition holds", which is what a Campaign is.

## Task

- Rename the user-facing vocabulary: Goal → Routine (루틴), Campaign → Goal (목표).
- Keep "목표" (goal) and the goal's target score distinct.
- Do not touch API paths, DB names or anything a running client depends on (the code rename has to go in steps because the name "goal" changes meaning).

## Action

- i18n catalogs: `frontend/src/i18n/catalogs/campaigns.ts` (all Campaign copy → Goal/목표; target score → 목표치), `chat.ts`, `workflows.ts`, `admin.ts`, `agentSetup.ts` (책임/Responsibilities → 루틴/Routines; "Goal" task source → Routine), `projectExecutions.ts` (`executions.objective` 목표 → 목적 to avoid a third meaning).
- `anygarden/campaigns/retrospective.py`: the room message a retrospective posts now says "Goal “…” retrospective".
- Docs: `docs/campaigns.md` rewritten with 목표/목표치 and a note on the former name; README link text in `README.md`, `README.ko.md`.
- Tests updated for the new strings: vitest (`CampaignSection`, `CampaignForm`, `campaignErrors`, `ManifestPanel`, `GoalForm`, `GoalsPanel`) and `e2e/campaigns.spec.ts`.

## Decisions

- Options for the recurring entity: **Routine** vs Schedule. Schedule names only the trigger and collides with Korean "일정"; the entity also owns assignee, spec, report room and auto-pause. Paperclip and Claude Code call this a routine.
- Options for the metric loop: **Goal** vs Objective (formal, OKR-flavoured) vs Mission (no sense of metric/deadline) vs Target (collides with the target score).
- Target score became **목표치** so "목표" always means the entity.
- Agent-facing text (cycle brief, MCP tool descriptions and errors) deliberately still says "campaign": the tool names (`record_campaign_attempt`, …) cannot change yet, and mixing "goal" text with "campaign" tool names would give the model two vocabularies. This deviates from the #939 phase-1 checklist; it moves with the tool rename in #941.
- Code identifiers (phases 2–3) split into #941: renaming `goal` → `routine` needs a data migration (`agent_goals`, `tasks.goal_id`, `project_executions.goal_id`) and a one-release API alias before `campaign` can take the name `goal`.
- Revisit if users still confuse 목표 (entity) and 목표치 (target score) in the form.

## Result

- UI and docs use 루틴/Routine and 목표/Goal everywhere the two entities appear; no "책임"/"캠페인" remains for them.
- vitest 1121 passed, Playwright e2e 84 passed, `npm run build` OK, `tests/test_campaigns_retrospective.py` 34 passed.
- Pending: code identifier rename (#941).

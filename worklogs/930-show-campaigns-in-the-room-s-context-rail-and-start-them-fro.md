# feat(campaigns): show campaigns in the room's context rail and start them from a form (#930)

- Commit: `2caf4c8` (2caf4c8b62eccb306e5348db5aa56ccb1b8df98b)
- Author: e7217
- Date: 2026-10-26T04:31:52Z
- PR: #930

## Situation

The campaigns backend (Phases 0–7 of plan-930) was complete, but the frontend had no UI for interacting with campaigns. The right-side context rail of a room showed goals and tasks, but the new `campaigns` resource was absent. Users had no way to create or monitor a metric-driven campaign from the web app.

## Task

Implement Phase 8 of plan-930-metric-campaigns.md: add all frontend primitives needed to expose campaigns in the room UI.

- `lib/campaigns.ts` — REST client, TypeScript types, and helper utilities mirroring `api/v1/campaigns.py`
- `hooks/useRoomCampaigns.ts` — React hook managing campaign list, recent attempts, WS event merging
- `components/right-rail/CampaignSection.tsx` — right-rail section: status badge, best score, budget/time left, last 5 attempts, action buttons, inline extend form
- `components/campaign/CampaignForm.tsx` + `campaignDraft.ts` — create-campaign dialog with mode-driven field visibility and client-side validation
- `i18n/catalogs/campaigns.ts` — en/ko string catalogue
- `lib/campaignFixtures.ts` — shared test fixtures
- Tests: vitest unit tests for each new module, one Playwright e2e test (`e2e/campaigns.spec.ts`)

## Action

- `lib/campaigns.ts` (242 lines): defined all TypeScript types (`Campaign`, `CampaignAttempt`, `CampaignStatus`, `CampaignMode`, etc.), the `OPEN_CAMPAIGN_STATUSES` set, `campaignStatusTone()` tone mapper, `campaignActions()` which returns valid action buttons per status, and CRUD/action functions (`listRoomCampaigns`, `createCampaign`, `runCampaignAction`, `extendCampaign`, `listCampaignAttempts`).
- `hooks/useRoomCampaigns.ts` (155 lines): fetch on mount, WebSocket `campaign.updated` / `campaign.attempt_updated` event merging via `upsertCampaign` / `upsertAttempt` helpers, stable `create` / `act` / `extend` callbacks.
- `components/right-rail/CampaignSection.tsx` (298 lines): `<CampaignStatusBadge>` renders a colored badge from `campaignStatusTone`; main section lists campaigns, each with title, status badge, best score vs. target, usable-token budget bar, time-left, and up to 5 recent attempts; action buttons delegate to `useRoomCampaigns.act`; an inline extend form handles token/deadline top-ups; `<CampaignForm>` is opened via `+` button in the section header.
- `components/campaign/CampaignForm.tsx` (215 lines) + `campaignDraft.ts`: dialog with two stages — mode picker first (because mode governs which termination inputs appear), then detail fields (title, purpose, metric name/direction/target, evaluator command/cwd/score-key, budget, optional deadline, optional trusted-eval). Advanced fields are collapsed by default. `buildCampaignInput` converts the draft to `CampaignCreateInput`; `validateCampaignDraft` returns per-field errors.
- `i18n/catalogs/campaigns.ts`: all UI strings in `en` and `ko`.
- `e2e/campaigns.spec.ts` (170 lines): stubs REST (`/api/v1/rooms/:id/campaigns`, POST, PATCH pause) and WebSocket with Playwright route interception; exercises opening the form, filling required fields, submitting, seeing the campaign appear in the rail, and pausing it.

## Decisions

**Mode picker first in the form**: The plan (§3.9) specifies "mode is chosen first because it decides how the campaign ends and so which inputs show." Target mode requires a metric target value; optimize mode hides it and requires a deadline instead. Showing both unconditionally would confuse users or let them submit an invalid combination. Alternative of showing all fields with conditional required-ness was considered and rejected as harder to reason about.

**Client-side validation mirrors server policy**: `validateCampaignDraft` replicates `CMP-01` (budget required, target-or-deadline required) on the client so the user gets inline field errors before hitting the network. The server is still the authority; client validation is a UX convenience only.

**`OPEN_CAMPAIGN_STATUSES` as a `ReadonlySet`**: Several UI branches (disable delete, show action buttons) depend on whether a campaign is still open. Encoding this as a typed set in the lib keeps it co-located with the types and easy to test, vs. scattered `===` checks throughout components.

**WS upsert guards on `state_revision`**: `upsertCampaign` only replaces the stored copy if the incoming `state_revision` is newer (or it's a new campaign). This prevents a stale WS push from overwriting a fresher optimistic update returned by a REST action call. The pattern mirrors the existing `useRoomGoals` hook.

**Fixtures extracted to `campaignFixtures.ts`**: Both the vitest unit tests and the e2e test need realistic campaign objects. Centralising them avoids drift between test suites. An alternative of inline factories per test file was rejected as duplication-prone.

## Result

- The right-side rail shows a "Campaigns" section for any room that has campaigns, with live status, best score, budget, and recent attempts.
- The `+` button opens a create-campaign dialog. Submitting a valid form POSTs to `POST /api/v1/rooms/:id/campaigns` and immediately shows the new campaign in the rail.
- Action buttons (pause, resume, stop, confirm, reject) appear per campaign status and call the appropriate PATCH endpoint.
- All new modules are covered by vitest; one Playwright e2e test validates the full create-and-pause flow against a stubbed API.
- Remaining: server-side Phases 9 (docs, live validation) and the live `verify-scenarios` run are out of scope for this commit.

# fix(ui): unify controls and complete agent onboarding (#710)

## Situation

The design-system rollout left custom control sizes and CSS precedence conflicts across desktop and mobile. Machine setup did not persist connection credentials for restart. Agent creation and settings could report failure after a successful save, overbook a machine, or display data from an earlier selection. Workspace UI also offered combinations the default daemon could not execute.

## Task

Apply the approved UI and onboarding improvements, then resolve the 14 concrete findings from the follow-up review while preserving existing saved settings and operational flows.

## Action

- Unified control sizes and typography handling, account preferences, collapse controls, and mobile drawer focus/portal behavior.
- Extracted agent creation and machine connection dialogs; added descriptions, initial permissions, room membership, and access to settings and activity.
- Added durable, user-scoped creation request identity in migration 077, atomic placement reservations, and stale-response protection for machine details and agent saves.
- Added server-authoritative workspace support options and a room-manager approval entrypoint. Unsupported external-folder combinations fail closed; actual daemon execution remains tracked in #491.
- Added `anygarden-machine connect` with private token input, atomic settings persistence, restart instructions, and integrated-node workspace registry selection.
- Added regression coverage for concurrent creation, delayed responses, save/refresh failures, model options, permissions, migration preservation, CLI persistence, and keyboard behavior.

## Result

- Frontend: 634 Vitest tests, 26 browser scenarios, and the production build passed.
- Focused backend and CLI checks: 262 tests passed.
- Read-only browser inspection covered nine routes in desktop/light/English and mobile/dark/Korean, plus machine/chat widths from 375 to 1920px.
- Full Ruff still reports 1,687 existing diagnostics; comparison against HEAD found no increased diagnostic counts in changed Python files or diagnostics in new Python files.
- The new connection guide requires a machine package release containing `connect`. The default daemon still does not support external-folder root/audit enforcement; no live remote-engine execution was claimed.

See [the detailed results](../docs/plans/2026-09-26-review-fixes-results.md) for the per-finding changes, evidence, and remaining work. PR checks provide the final branch validation.

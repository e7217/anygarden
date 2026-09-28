# feat(agent-settings): show latest turn result beside process state (#716)

- Commit: `0b293d1` (0b293d157af53a34fcf1d27c45fedb9784443274)
- Author: Changyong Um
- Date: 2026-09-28T11:00:19+09:00
- PR: #716

## Situation

Agent settings showed only process connectivity in Overview (Online/running). In the Pi session bug (#714), the agent stayed Online while every turn failed with `missing_terminal_event` and then `POLICY_DENIED`. The reason was visible only in Activity, the last collapsed section of a long dialog, so "connected" read as "answering".

## Task

- Show the latest turn's result and time next to State, and keep the Online/offline indicator unchanged.
- On failure, give a short human-readable reason and a direct path to the detailed Activity event.
- Distinguish engine failure, model connection failure, and execution-policy block when the code allows.
- Never expose raw provider errors, secrets, or prompts in the summary.
- Cover the case where the process is Online and the latest turn failed.

## Action

- `packages/cluster/frontend/src/components/agent-settings/turnHealth.ts`: pure helpers.
  - `latestTurnHealth(turns)` reads the newest `splitLogs` turn. It prefers the authoritative `handler_finished` outcome, so a #422 failure notice (`response_sent`) is not counted as a reply.
  - `classifyTurnFailure` sorts failures into model_connection / policy / timeout / busy / engine.
  - `summaryErrorCode` admits only closed codes (UPPER_SNAKE or lower_snake, plus the `UNSUPPORTED_RUNTIME:` prefix) and returns null for free-form text.
- `RecentTurnSummary.tsx`: renders a `dt`/`dd` "Latest reply" row inside the Overview list. It shows a status dot, a text label, and the time. On failure it adds the category, a reason, the closed code, and a "View in Activity" link. It has its own `useAgentActivity` while the dialog is open.
- `OverviewPanel.tsx`: new `recentTurn` slot rendered right after State.
- `AgentSettingsDialog.tsx`: wires the summary. The link sets `focusRequestId` and jumps to (and opens) the Activity section.
- `ActivityPanel.tsx`: new `focusRequestId` prop. It expands that turn once loaded and scrolls it into view. Rows carry `data-request-id`.
- `i18n/catalogs/admin.ts`: en/ko strings under `admin.overview.recentTurn.*`. Model-connection reasons reuse the existing `admin.activity.*` messages.
- Tests: `turnHealth.test.ts` (18) and `AgentSettingsDialog.turnHealth.test.tsx` (7). The dialog tests cover Online + failed, the category labels, redaction of free-form text, the success case, and focus into Activity.

## Decisions

- **Client-side derivation vs a new server field**: the activity API already returns every needed field. `ActivityPanel` groups turns client-side for the same reason (#222 §3.2). A server "last turn" column would duplicate that grouping and need a migration.
- **Separate row vs changing the State indicator**: the issue asks to keep process state and response health as separate signals. Folding failure into the State dot would hide the fact that the process is still connected, which is itself diagnostic (the #714 fence case).
- **Closed-code allow-list for the summary**: `handler_wrapper` can report `str(exc)` for unexpected exceptions, which could include provider bodies. So the summary shows a category and fixed copy. It shows a code only when the text matches the receipt-code shape; free-form text stays in the Activity details.
- **Policy reason copy points to stop/start**: `POLICY_DENIED` after an `unknown` receipt is cleared only by a new launch generation (documented in #714's runbook change). The copy says it is "often" the cause, because authorization loss yields the same code.
- **Refresh cadence**: the summary reuses `useAgentActivity`, which polls only while a turn is pending. A turn that fails after the dialog is already open and idle appears on reopen or refresh. Revisit if operators keep the dialog open as a monitor.

## Result

- The Overview shows "Latest reply" next to State: Replied, Failed (with category + reason + code), In progress, Cancelled, or No turns yet.
- "View in Activity" opens Activity with the failed turn expanded. This was verified in a browser against a scratch backend with seeded failed turns (`POLICY_DENIED`, `missing_terminal_event`).
- Frontend: 94 files / 735 tests passed; `npm run build` (tsc + vite) succeeded.

# feat(ui): show how to install an engine and detect it again (#902)

- Commit: `4c4ec54` (4c4ec543e769f2b3646a0d6ed421bfa086616503)
- Author: Changyong Um
- Date: 2026-10-19T15:33:19+09:00
- PR: #902

## Situation

When a machine had no engine the UI displayed a static sentence — "No engines detected. Install and authenticate an engine on this computer, then restart the daemon to detect it." — which left a new user stranded: there was no command to copy, the process named was `daemon` while users know it as `anygarden start`, and there was no way to trigger re-detection without actually restarting the node. The "New Agent" button stayed disabled with no actionable path forward.

## Task

- Replace the dead-end `noEnginesHint` string with an interactive `EngineSetupHint` component that surfaces the install commands for both supported engines (Codex, Pi) with copy buttons.
- Add a "Detect again" button that calls `POST /api/v1/machines/:id/engines/rescan` and polls `GET /api/v1/machines/:id/engines` until an engine appears (or a ~90 s timeout expires), then calls `onDetected()` to refresh the parent.
- Wire the new component into both the machine detail panel (`AdminMachines`) and the create-agent dialog (`CreateAgentDialog` / `SidebarCreateAgentDialog`).
- Cover the happy-path poll, timeout, and rescan-failure cases with unit tests; add an E2E scenario for the full flow on a mobile viewport.

## Action

- **New file `EngineSetupHint.tsx`** (`packages/cluster/frontend/src/components/agent-settings/EngineSetupHint.tsx`): renders a card with title/body text, two engine rows each showing the install command in a `<code>` block with a copy button, and a "Detect again" `<Button>` that drives the async detect/poll loop; handles clipboard failure, poll timeout, and rescan API error.
- **New test `EngineSetupHint.test.tsx`** (same dir): four vitest/jsdom cases — static install commands display; successful poll fires `onDetected`; timeout shows "No engine found yet"; rescan failure shows "Could not ask the machine".
- **`AdminMachines.tsx`** line 834: replaced the `<span>` with `<EngineSetupHint machineId onDetected>` and wired `onEnginesDetected` prop from `CreateAgentDialog` (line 998–999).
- **`CreateAgentDialog.tsx`**: added `onEnginesDetected?: () => void` prop; replaced the `<p>` status text with `<EngineSetupHint>` when engines empty and not loading (line 246).
- **`SidebarCreateAgentDialog.tsx`**: destructured `refresh` from `useMachineDetail`; passed `onEnginesDetected={() => void refreshDetail()}` to `CreateAgentDialog`.
- **`admin.ts` i18n**: removed `admin.machines.noEnginesHint` (en + ko) — superseded by `agentSetup.engineSetup.*`.
- **`agentSetup.ts` i18n**: added nine new keys (`title`, `body`, `codex`, `pi`, `copy`, `detect`, `detecting`, `pending`, `failed`) in both en and ko.
- **`agent-setup.spec.ts`** E2E: extended `setup()` to accept an `engines` factory and `openDialog` flag; added `[NODE-16]` test that simulates install after the dialog opens, clicks "Detect again", and asserts the hint disappears and the "New Agent" button enables.

## Decisions

No plan file was present; rationale is inferred from the diff and commit body.

- **Inline install commands vs. link to docs**: commands are shell strings that change rarely and are the primary action; embedding them with copy buttons removes one context-switch. A docs link would still leave users needing to find and copy the command manually.
- **Poll + rescan API vs. long-poll or WebSocket**: the rescan endpoint already existed (from #... prior work); polling at 3 s intervals is simple, avoids a new WebSocket subscription, and 30 attempts × 3 s ≈ 90 s is generous for a first Pi npm install (the longest expected case, noted in the comment).
- **`onDetected` callback vs. internal state refresh**: the component does not own the machines/engines data — the parent does. Handing the reload back to the parent via a callback keeps the component stateless with respect to data and composable in both `AdminMachines` and `CreateAgentDialog`.
- **Shell commands kept out of i18n catalogs**: the commands (`npm i -g @openai/codex && codex login`, etc.) are version-sensitive technical strings, not UI copy. Keeping them as JS constants avoids accidental translation.
- **Assumption**: if the rescan API contract or the `/engines` polling endpoint changes, `POLL_MS` / `POLL_ATTEMPTS` constants in `EngineSetupHint.tsx:10–11` and the E2E mock at `agent-setup.spec.ts:20` are the places to update.

## Result

- Machines with no engine now show a styled card listing the Codex and Pi install commands with copy buttons, and a "Detect again" button that triggers re-detection without a daemon restart.
- The `[NODE-16]` E2E scenario and four unit tests cover happy-path, timeout, and failure states.
- The old `admin.machines.noEnginesHint` string (which named `restart the daemon`) is removed; no other component referenced it.
- `onEnginesDetected` is plumbed through `CreateAgentDialog` and both of its parent callers so the engine list refreshes in-place after detection.

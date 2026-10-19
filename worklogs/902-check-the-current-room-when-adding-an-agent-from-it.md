# feat(agents): check the current room when adding an agent from it (#902)

- Commit: `6635dac` (6635dac)
- Author: Claude Opus 5.5
- Date: 2026-10-19
- PR: #902

## Situation

When a user had a room open in the sidebar and opened the "Add Agent" dialog, the currently-viewed room was not pre-checked in the room membership list. As a result, the newly created agent would not be a member of the room the user was working in, and would therefore never receive mentions there unless the user manually noticed and checked the room in the dialog.

## Task

- Add an `initialRoomIds` prop to `CreateAgentDialog` and `SidebarCreateAgentDialog` so callers can seed which rooms start checked.
- Pass the currently-selected room from `Sidebar` as `initialRoomIds` when opening the dialog.
- Ignore DM rooms and unlisted rooms on submit (only regular listed rooms should be auto-checked).
- Add vitest coverage for the new prop in both dialog components.

## Action

- `src/components/SidebarCreateAgentDialog.tsx`: added optional `initialRoomIds?: string[]` prop (with JSDoc), destructured it, and forwarded it to the inner `<CreateAgentDialog>` via `initialRoomIds={initialRoomIds}`.
- `src/components/Sidebar.tsx` (~L1448): passed `initialRoomIds={selectedRoom ? [selectedRoom] : undefined}` to `<SidebarCreateAgentDialog>`.
- `src/components/CreateAgentDialog.tsx` (~L67–81): accepted `initialRoomIds` and used it to initialise the `agentRooms` state (`new Set(initialRoomIds ?? [])`) instead of always starting with an empty set.
- `src/components/CreateAgentDialog.test.tsx`: added 16 lines covering the case where the dialog opens with a room pre-checked.
- `src/components/SidebarCreateAgentDialog.test.tsx`: added 25 lines (+5 hunks) verifying the prop is forwarded correctly.
- All 20 vitest tests pass; TypeScript build clean (`TSC=0`).

## Decisions

- **Pass `initialRoomIds` as a prop rather than reading global state inside the dialog** — the dialog components are reused in multiple contexts (sidebar, admin panel). Keeping them stateless with respect to room selection avoids coupling them to the sidebar's navigation state and makes them easier to test.
- **`undefined` vs `[]` for "no pre-selection"** — `undefined` was chosen as the default (no prop passed) to distinguish "caller has no opinion" from "caller explicitly wants nothing checked". The dialog falls back to an empty set in both cases, so there is no behavioural difference at the moment, but the distinction keeps the API honest.
- **DM and unlisted rooms excluded on submit** — the commit body states this constraint. DMs are 1-to-1 rooms not meant for general agent membership; pre-checking them would add an agent to a private DM without the user explicitly confirming. The filter happens on submit, not on display, so the UX is consistent with the existing room picker behaviour.
- **Option considered and rejected — reading `selectedRoom` from a shared context inside `CreateAgentDialog`**: would have avoided threading the prop, but would have made the dialog implicitly depend on sidebar navigation state, breaking reuse outside the sidebar and making tests harder to write.

The plan file (`.tmp/plan-902-onboarding-engine-guidance.md` §4 step 4) specified exactly this approach: "`initialRoomIds` prop (Sidebar → SidebarCreateAgentDialog → CreateAgentDialog)" and called for vitest coverage confirming the pre-checked state.

## Result

- Agents added while a room is open are automatically joined to that room.
- 20 unit tests pass (no regressions).
- TypeScript compilation clean.
- DM rooms and unlisted rooms remain excluded from auto-membership on submit.

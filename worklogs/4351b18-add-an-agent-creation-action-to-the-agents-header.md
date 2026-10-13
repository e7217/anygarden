# feat(sidebar): add an agent-creation action to the agents header

- Commit: `4351b18` (4351b1855b1837d86b9868094eb7bf09a3599374)
- Author: Changyong Um
- Date: 2026-10-13T16:49:44+09:00
- PR: —

## Situation

Admins who wanted to create an agent had to navigate through Admin → Machines → select a machine → create agent — a four-step flow hidden deep in the admin area. The agents sidebar section was also invisible until at least one agent existed, making the feature effectively undiscoverable for new setups.

## Task

- Add a `+` (Add agent) button to the Agents section header in the sidebar, visible only to admins.
- The section itself must render for admins even when no agents exist yet.
- The new dialog must pick the right machine automatically: silent selection when one machine is online, a picker when several are, and a redirect to the machines page when none is.
- Preserve in-progress form state when switching machines in the multi-machine picker.

## Action

- `Sidebar.tsx`: Changed the guard condition from `agentDMs.length > 0` to `isAdmin || agentDMs.length > 0`, making the Agents section always visible to admins. Restructured the header from a single `<button>` to a `<div>` flex row holding the expand toggle and a new `<Plus>` icon button (`data-testid="sidebar-add-agent"`). Wired `createAgentOpen` / `setCreateAgentOpen` state down to `AgentDMListAdmin` instead of rendering the new dialog in the section directly.
- `AgentDMListAdmin` (inside `Sidebar.tsx`): Added `createAgentOpen` / `onCreateAgentOpenChange` props and a `<SidebarCreateAgentDialog>` mount. The dialog stays mounted while the section is collapsed so open dialogs survive toggling. Also surfaced `createAgent` from `useAgents()`.
- `SidebarCreateAgentDialog.tsx` (new): Thin orchestration wrapper that calls `useMachines()` to get the machine list, refreshes on each open, computes `onlineMachines`, derives the active `machineId` (auto-selects the sole online machine; resets to first online if the selected one goes offline), loads `useMachineDetail` for the chosen machine's engine list, and then delegates to `CreateAgentDialog`. When `machineId` is null (no machine online or still loading) it renders a minimal fallback dialog with a loading/error/empty-state message and either a Retry button or an "Open machines" nav button.
- `CreateAgentDialog.tsx`: Extended props with `machineOptions`, `onMachineChange`, `enginesLoading`. When `machineOptions` is provided and has more than one entry, step 1 renders a `<Select>` for the Machine field. Engine state is reset to the first available engine when the engine list changes (from machine switching), instead of only on `open` change. Dialog title switches to the shorter `admin.machines.createAgent` key when a picker is present.
- `i18n/catalogs/chat.ts`: Added `chat.addAgent`, `chat.addAgentNoMachine`, `chat.addAgentMachinesFailed`, `chat.goToMachines` in both EN and KO.
- `i18n/catalogs/agentSetup.ts`: Added `agentSetup.machine` label in EN and KO.
- Tests: `SidebarCreateAgentDialog.test.tsx` (new, 3 cases: no-machine redirect, single-machine silent pick, multi-machine picker). `Sidebar.test.tsx` (2 new cases: admin sees add-agent button even with no agents; non-admin does not). `CreateAgentDialog.test.tsx` (2 new cases: draft survives machine switch; picker hidden for single-machine).

## Decisions

- **Separate `SidebarCreateAgentDialog` vs. inline logic in `Sidebar`**: A dedicated component keeps `Sidebar.tsx` from growing further and makes the machine-resolution logic independently testable. The alternative of inlining everything in `AgentDMListAdmin` was rejected because it mixes layout concerns with data-fetching concerns.
- **Auto-select vs. always show picker**: Showing a picker when only one machine is available adds friction for the common case. Auto-selecting the single online machine with a visible "Runs on X" label is less noisy. The picker appears only when a choice is meaningful.
- **Fallback dialog instead of disabled button**: Hiding the `+` button when no machine is online would leave admins confused ("why is there no button?"). A dialog that explains the situation and offers a direct path to the machines page is more informative. (unclear from available context: whether a toast notification was considered as an alternative to the fallback dialog.)
- **`createAgentOpen` owned by `Sidebar`, not `AgentDMListAdmin`**: The `+` button lives in the section header rendered by `Sidebar`, so it needs to set state that controls the dialog. Lifting state up avoids prop-drilling the setter while keeping the click handler co-located with the button.
- **`AgentDMListAdmin` stays mounted when collapsed**: Previously unmounting it on collapse would have torn down the open create/settings dialogs. Passing `dms={[]}` instead of unmounting is the simplest fix.

## Result

- Admins can open the agent-creation dialog directly from the sidebar without visiting the machines page.
- The Agents section is now visible to admins even before any agent is created.
- Single-machine setups get a zero-friction flow; multi-machine setups get a picker; offline setups get a clear explanation and a redirect link.
- Switching machines inside the dialog preserves the agent name and other step-1 draft state.
- 7 new test cases cover the three machine-count scenarios, the admin/non-admin visibility gate, the draft-persistence behavior, and the single-machine picker-suppression.

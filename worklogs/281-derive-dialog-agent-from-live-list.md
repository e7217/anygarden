# fix(agents): derive AgentSettingsDialog agent prop from live agents list (#281)

- Commit: `0d72c63` (0d72c6342558452cd2b5f04b9bdec55632641432)
- Author: Changyong Um
- Date: 2026-04-27T18:42:29+09:00
- PR: #281

## Situation

Toggling the Model / Reasoning / Collaboration `<select>` inside the
unified `AgentSettingsDialog` flickered back to the previous value or
failed to update at all. Closing and reopening the dialog showed the
new value correctly, so the symptom looked like a transient UI glitch
but it surfaced consistently for any in-dialog edit. Spotted as a
non-blocking minor regression while reviewing #279's collaboration-
mode landing — explicitly called out as "not a regression of this
work, candidate for a separate PR".

The dialog itself was correct: `OverviewPanel` already renders the
select as `<select value={agent.model ?? ''}>`, a controlled
component that reads directly from prop. The problem had to be
upstream of the dialog.

## Task

- Locate the upstream cause without disturbing the working
  `OverviewPanel` controlled-select wiring.
- Apply a fix that removes the stale-prop class of bug everywhere it
  could happen, not just for the model select that prompted the
  report.
- Leave a regression marker (test + comments) so the next dev who
  touches dialog parent state sees the canonical pattern.

## Action

- Verified call sites: `Sidebar.tsx:995` and `AdminMachines.tsx:221`
  both held the open dialog's agent as `useState<Agent | null>(null)`,
  populated by an `agents.find` lookup at open-time. After
  `updateAgent → fetchAgents` resolved, the canonical `agents` list
  refreshed but the snapshot did not, so the prop fed to the dialog
  remained pinned to the open-time copy.
- Both call sites now store only `settingsAgentId: string | null` and
  derive the Agent record from the live list with `useMemo`. The
  `onOpenChange` handler additionally clears the tracked ID on close
  so an externally-deleted agent doesn't leave the next open with a
  null derived prop.
- The same comment block in both files explains the rationale,
  references #281, and points at the regression test.
- Added a `parent state pattern (#281)` block to
  `AgentSettingsDialog.test.tsx` that wires up a tiny wrapper
  component using the canonical pattern, mutates the agents list to
  simulate a `fetchAgents` resolution, and asserts the model select
  reflects the change without reopening. The test mocks
  `fetchEngineCatalog` with a real two-model catalog so the model row
  actually renders (the existing suite uses `null` to keep that row
  hidden, which doesn't help here).

## Decisions

Considered three options for the fix shape (plan §3.2):

- **(A) ID + derive on the parent** — chosen. Two-line change at each
  site, no extra abstraction, single source of truth for the agent
  record (the canonical `agents` list).
- **(B) Snapshot + sync `useEffect`** — rejected. Same outcome but
  with two states tracking the same fact, adding a "which is true?"
  judgment call every time the parent re-renders.
- **(C) Have the dialog subscribe to `useAgents` directly** —
  rejected. Couples the dialog to its data source, breaks the
  existing `agent: Agent | null` prop contract, and complicates
  testing.

The decisive observation was that the comment block already on
`Sidebar.tsx:990-993` ("agent_id → full record lookup so the dialog
always sees the latest Agent fields after an update") had described
the desired behaviour for some time — the implementation just wasn't
matching its own intent. Option (A) makes the code match the comment
that was there to begin with.

Test placement — kept the regression test in
`AgentSettingsDialog.test.tsx` rather than `Sidebar.test.tsx` /
spinning up a new `AdminMachines.test.tsx`. The Sidebar suite mocks
`useAgents` with a minimal shape and would need substantial new
plumbing to exercise the dialog flow; the new test instead exercises
the *pattern* the call sites are supposed to follow, which is what we
actually want enforced. PR review and the comment-as-callout in both
call sites stand in for direct enforcement that Sidebar /
AdminMachines stick with the canonical shape.

`onOpenChange` resetting `settingsAgentId` — added defensively. It's
not necessary for the original bug (snapshot vs derive), but it
protects against the related case where an agent gets deleted while
the dialog is closed: without the reset, the next open would briefly
show "No agent selected" before the admin clicked something else.
The cost is one extra line; the alternative (an external-deletion
race) is harder to reproduce but real.

## Result

- Frontend tests 348/348 green (was 347 — added the new
  `parent state pattern (#281)` case).
- `npm run build` clean (tsc + vite production build with no new
  warnings beyond the pre-existing chunk-size advisory).
- Verified manually that the diff in both call sites is structurally
  identical, so a reader checking one site for context can rely on
  the other looking the same.
- Out of scope and intentionally not pursued:
  - Extracting the pattern into a shared `useDialogAgentRef` hook —
    only two call sites, premature abstraction (plan §3.2 (C)).
  - Direct Sidebar / AdminMachines integration tests — heavy mock
    surface area for a one-line shape change. The wrapper-based
    pattern test gives us the behavioural guarantee we wanted at a
    fraction of the setup cost.

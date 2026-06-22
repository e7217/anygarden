# fix(agent-settings): seed AGENTS.md editor only on agent id change (#479)

- Commit: `f287880` (f287880f07f1cac6aebaeaf2ab278b7a0ccc337d)
- Author: Changyong Um
- Date: 2026-06-22T22:09:52+09:00
- PR: #479

## Situation

In the agent settings dialog's manifest panel, editing AGENTS.md (the agent's
identity file) kept resetting to the server value — repeatedly, roughly every
1.5 seconds, whenever any agent was in a transitional state. Users lost their
in-progress edits (the reported "편집 중 초기화").

## Task

- Stop the editor working copy from being clobbered while the dialog is open.
- Preserve the intentional #281 pattern (the parent derives a live `Agent`
  object from the agents list so the dialog's display values stay in sync).
- Keep the open / agent-switch seed behavior intact.

## Action

`ManifestPanel.tsx` — gated the seed effect (`packages/cluster/frontend/src/components/agent-settings/ManifestPanel.tsx`) on the agent's stable `id` via a `seededAgentIdRef`:
- The effect previously ran `loadInitial()` on every change to the `agent` prop's object identity (`[agent, loadInitial]`), and `loadInitial` re-`setFiles(...)`-ed the whole working copy from `agent.agents_md`, discarding edits.
- Now it seeds only when `seededAgentIdRef.current !== agent.id` (open / agent switch); a same-id refresh early-returns. Mirrors the existing `agent?.id`-gated `expandedPaths` effect. Reset the ref to `null` when `agent` is null so a future mount re-seeds. Re-opening for the same agent is covered by the dialog unmounting the panel on close.

`ManifestPanel.test.tsx` — added a `#479` describe block:
- "preserves an in-progress AGENTS.md edit when the agent prop is replaced with a new object of the same id" — edits AGENTS.md, rerenders with a new same-id `Agent` (simulating the poll), asserts `fetchAgentFiles` is NOT called again and the edit survives. (Red before the fix: `fetchAgentFiles` called twice.)
- "re-seeds AGENTS.md when the agent prop switches to a different id" — guards the no-regression direction.

## Decisions

- **`agent.id`-gated seed (ref guard) over alternatives.** The codebase already
  solved the same class of problem one effect below — `expandedPaths` is keyed
  on `agent?.id`, not the object. Reusing that pattern fixes the root cause
  (over-eager re-seed) with the smallest, most consistent change.
- **Rejected: stabilize/snapshot the `agent` in the parent or panel.** That
  would directly regress #281 — the dialog's model select / badge must track
  `setAgents` updates. The fix separates "live display data" (still the fresh
  prop) from "editor seed" (snapshot per id).
- **Rejected: pause the #219 poll while the dialog is open.** Treats the
  symptom; mutation-driven `fetchAgents` calls would still clobber edits.
- **Assumption:** Radix `Dialog open={open}` unmounts the panel on close, so a
  reopen remounts and re-seeds. Verified by the passing suite (panel is
  rendered directly in tests; reopen path is the mount path).

## Result

Same-id `agent` refreshes no longer reset the editor; a real agent switch still
re-seeds. Full frontend suite (47 files / 437 tests) and `tsc -b` pass, with the
#281 parent-state-pattern test green. Edits now persist through the transitional
poll and through agent mutations while the dialog stays open.

# refactor(admin): unify AGENTS.md into agent manifest file tree (#109)

- Commit: `0053636` (005363664067ce56a9439a389e4bd4eeb877af03)
- Author: Changyong Um
- Date: 2026-04-18T17:51:09+09:00
- PR: #109

## Situation

`AgentEditDialog` split its UI into two sections: a dedicated `AGENTS.md` textarea at the top and a files-tree + editor below. That split mirrored the backend storage difference — `Agent.agents_md` is a column on the `agents` row while everything else lives in the `agent_files` table with a prefix whitelist — but the admin had no reason to care which storage site a given "manifest" used. The double-panel layout was also the only part of the dialog that kept its own private dirty flag (`agentsMdDirty`), its own save branch, and its own empty-state/clear semantics, so the surrounding code carried two parallel bookkeeping paths for one product concept.

## Task

- Collapse the two panels into a single file-tree + editor, presenting `AGENTS.md` as a "virtual" entry pinned at the top of the tree.
- Keep backend schema unchanged — `Agent.agents_md` remains a column, the `agent_files` table and its path whitelist remain authoritative. Only the UI's presentation and save routing change.
- Make `AGENTS.md` always-present (null on the server renders as empty content on the client), non-deletable (no trash icon; saving empty content is the "clear to null" gesture), and non-creatable via the "New file" form.
- Preserve every other behavior: upload / download (#98), engine prefix groups, dirty indicator dot, Save-then-resync, placeholders, DESIGN.md polish.

## Action

All changes in `packages/cluster/frontend/src/components/AgentEditDialog.tsx`:
- Added the `AGENTS_MD_PATH` constant (`AgentEditDialog.tsx:113`) so every touchpoint matches on the same named literal rather than scattered strings.
- Extended `WorkingFile` with `virtual?: boolean` (`:125`) — a single discriminator for the five places that branch on "this is the AGENTS.md row" (render, trash gate, save routing, delete guard, new-file rejection).
- Added `makeAgentsMdFile(md, updatedAt)` (`:139`) to build the virtual row from the `Agent.agents_md` prop. Critically, `originalContent` is `null` when `md` is `null` so that clearing the editor and saving later produces `agents_md: null` at the server; `content` is always a string so the textarea stays controlled.
- Removed `agentsMd` / `agentsMdDirty` state and the `handleAgentsMdChange` callback. The working-copy `files` array now carries the edit state for AGENTS.md the same way it does for every other row.
- `loadInitial` and `resyncAfterSave` prepend (or refresh) the virtual row around `fetchAgentFiles` results. `resyncAfterSave` intentionally does NOT re-read `agent.agents_md` (a stale snapshot) — it promotes the just-saved local content to `originalContent` to preserve the post-save "not dirty" state.
- `selectedPath` defaults to `AGENTS.md` when nothing is remembered, so opening the dialog lands on the agent's identity first.
- `groupedFiles` (`:290`) now emits a headerless first group for virtual rows and filters `!f.virtual` out of the prefix-based groups. Rendering checks `group.label` before emitting the group header, so the virtual group appears without a category label above it.
- File row JSX (`:686`): added a leading `FileText` icon on virtual rows, gated the trash button behind `showTrash = !f.virtual`, and added `data-virtual="true"` for test assertions.
- `handleAddFile` (`:346`) rejects `path === AGENTS_MD_PATH` with a clear message ("already exists at the top of the tree") so the admin gets a better error than the generic prefix-validation or duplicate-file messages.
- `handleRemoveFile` (`:495`) guards against removing virtual rows as belt-and-braces in case a future caller wires up a keybinding or context-menu.
- `handleSave` (`:510`) now routes by path inside a single dirty-files loop: virtual AGENTS.md goes to `updateAgent({agents_md, agents_md_set: true})`, everything else to `upsertAgentFile`. The deletion pass skips virtual rows.
- Editor textarea placeholder is now populated from the previous AGENTS.md textarea when the selected row is virtual, preserving the "define the agent's role" hint.
- Test suite in `AgentEditDialog.test.tsx` gained 6 new cases (virtual row always present, default selection, no trash, save routing with `agents_md_set`, empty content → null clear, `AGENTS.md` rejection from the new-file form) and updated the Download test to click the skill row first since AGENTS.md is now the default-selected entry.

## Decisions

Design rationale was pre-written in `.tmp/plan-109-unify-agents-md-into-file-tree.md`; the load-bearing threads:

- **UI-only refactor vs column-to-table migration.** The plan weighed whether to go all the way and move `agents_md` into the `agent_files` table under a reserved path. Rejected for this PR — it would require an alembic migration, touch AgentOut/AgentUpdate, add an exception to the `agent_files.path` whitelist, and reopen the materializer-vs-column path story. All of that is orthogonal to the admin UX problem. If a third "virtual" entry ever appears (e.g. a global config snippet) and the UI branch proliferates, revisit.
- **`virtual: boolean` flag vs path-string comparison.** The discriminator shows up in five places (render, trash render gate, save routing, delete guard, new-file rejection). A single boolean is dramatically cheaper to audit than five string equality checks, and the field survives future renames of the virtual path without cascading updates. `AGENTS_MD_PATH` still exists as a named constant so call sites can express intent when a path value is required.
- **Clear UX: empty-and-save vs separate "Clear content" button.** Chose empty-and-save because the `agents_md` column already encodes this as `null`, and the existing save loop (with the `_set` flag idiom) flushes the intent correctly. A second button would add UI without adding power, and "delete" language on a row that can't be deleted would be misleading.
- **Default-selected row: AGENTS.md vs first alphabetical.** AGENTS.md is the agent's identity — the thing the admin most often wants to see first. The previous sort-by-name default gave whichever file happened to alphabetize earliest; switching to AGENTS.md makes the dialog open on purpose. The "remember last-selected path" behavior is preserved, so subsequent opens still honor manual navigation.
- **Virtual group rendering: prepend-to-groupedFiles vs separate JSX block.** Prepending a headerless group keeps the row-render loop single-source-of-truth (trash, hover, selection, dirty indicator). A separate JSX block would have duplicated that loop and drifted over time.

Assumptions to revisit if violated later: (1) only one virtual entry exists — if a second appears, the virtual group's headerless rendering may need to add a label; (2) the server-side path whitelist already excludes `AGENTS.md` so the client rejection is belt-and-braces rather than load-bearing (confirmed against `doorae/agent_files.py`).

## Result

- `AgentEditDialog` now renders a single unified tree. Opening on an agent with `agents_md=null` shows AGENTS.md selected at the top with an empty editor and the original placeholder guidance.
- Editing AGENTS.md and Save: `updateAgent` is called with `{agents_md, agents_md_set: true}`, no `upsertAgentFile` call. `bump_generation` semantics on the backend are unchanged (this is the same code path #101 already used).
- Clearing AGENTS.md to empty and Save: server receives `agents_md: null`. Subsequent open shows the row empty with placeholder restored.
- Trash icon and "New file" path `AGENTS.md` behave as designed.
- Tests: AgentEditDialog suite grew from 4 to 10 cases; full frontend suite 183/183 (was 177 prior to this PR). `npm run build` passes.
- Line count net +161 (expected from the plan's optimistic -100 estimate): the added branches, comments, and test coverage outweigh the removed top-section JSX and removed `agentsMd` state. Product-goal (unify UX, simplify mental model) was the actual objective and is met.
- Out of scope, tracked for follow-ups: moving `agents_md` into `agent_files` at the schema level, default-template seeding for new agents, rename-in-tree, and any keyboard shortcut for toggling between AGENTS.md and last-edited file.

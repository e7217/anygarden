# refactor(rooms): absorb search + artifacts entries into RoomHeader (#329)

- Commit: `59f14f1`
- Author: Changyong Um
- Date: 2026-04-30 (Phase 3 of #329)
- PR: pending (issue #329)

## Situation

`ChatPage.tsx` had two slim rows that paid for themselves in vertical space without earning it back in functionality:

- A search button row above `ChatArea` — the same dialog is already triggered globally via `⌘K` (`ChatPage.tsx:148`), so the visible row was just a discoverability hint.
- An "산출물" (artifacts) row below `ChatArea` whose comment explicitly noted that "the '공유 파일' entry point moved to the right-rail FilesSection". The right-rail migration happened in #302, but the legacy artifacts row and the orphan `RoomSharedFilesDialog` mount were never removed.

Phase 3 of #329 reclaims that vertical space and consolidates the entry points into `RoomHeader` / `RoomSettingsMenu`.

## Task

- Move the search trigger into `RoomHeader` as a small icon button with a `⌘K` tooltip, so the discoverability hint stays without owning a row.
- Move the artifacts trigger into `RoomSettingsMenu` so it sits alongside the existing room actions; the header strip should not grow another inline icon.
- Remove the dead `sharedFilesOpen` state and the orphan `RoomSharedFilesDialog` mount, since nothing wrote to that state after the right-rail migration.
- Keep the right-rail `FilesSection` as the canonical entry point for shared files; this PR does not touch the rail.

## Action

- `packages/cluster/frontend/src/components/RoomHeader.tsx`
  - Imported the `Search` lucide icon.
  - Added `onSearch?: () => void` and `onShowArtifacts?: () => void` props with explanatory JSDoc referencing #329 Phase 3.
  - Rendered the search button as an 8×8 ghost icon between the agent-liveness badge and `RoomSettingsMenu`, using the same `hover:bg-black/5` convention as the surrounding controls. Title text is `"Search messages (⌘K)"` so the shortcut stays discoverable.
  - Forwarded `onShowArtifacts` to `RoomSettingsMenu`.
- `packages/cluster/frontend/src/components/RoomSettingsMenu.tsx`
  - Imported `Image as ImageIcon`.
  - Added `onShowArtifacts?: () => void` to the props interface (with comment explaining the no-admin-gate intent) and a corresponding entry in the `safeActions` builder. The existing "if every handler is undefined → render nothing" check covers the new prop automatically because it filters through `safeActions.length`.
- `packages/cluster/frontend/src/pages/ChatPage.tsx`
  - Removed the two legacy row divs (the search bar and the 산출물 button).
  - Removed `sharedFilesOpen` state and the `RoomSharedFilesDialog` mount + import — nothing called `setSharedFilesOpen` so the dialog could never open.
  - Removed the `Search` and `Image as ImageIcon` lucide imports that only the deleted rows used.
  - Wired `onSearch={() => setSearchOpen(true)}` and `onShowArtifacts={() => setArtifactsOpen(true)}` on `RoomHeader`.

`RoomArtifactsDialog` mount is retained — only its trigger moved.

## Decisions

The plan (`.tmp/plan-329-frontend-responsive-layout.md` §3.2 decision C) weighed three options:

- **C1 (chosen)** — remove both rows, push search into `RoomHeader` directly, push artifacts into `RoomSettingsMenu`. Reclaims the vertical space, single home for header-row actions, one menu home for less-frequent room actions.
- **C2** — keep the rows but `hidden md:flex` them. Rejected: the rows have low information value at every viewport, not just narrow ones; and the comment in the legacy code already flagged the artifacts row as needing cleanup post-migration.
- **C3** — add an artifacts section to the right rail. Partially rejected: the rail already hosts three sections (Goals / Tasks / Files) and adding a fourth pushes the rail closer to the same overflow problem we're trying to avoid. The settings menu is a better fit because artifacts is a low-frequency, glance-once action.

Decisive observation: the existing `RoomSettingsMenu`'s "render nothing when no handlers wire up" pattern means we get the new menu entry for free without changing menu visibility logic — `onShowArtifacts` simply joins the `safeActions` list. That kept the change small enough to land alongside the row-deletion in one PR.

The dead-state cleanup (`sharedFilesOpen`, `RoomSharedFilesDialog` mount) was originally a separate item on the plan ("dialog mounted for deep-link compatibility"), but a quick `grep` showed no caller wrote to `setSharedFilesOpen` after the right-rail migration — the deep-link compat note was speculative. Removing the dead mount kept the PR honest and avoids accumulating zombie code.

Assumptions to revisit if violated:

- Search discoverability survives the move from a labelled row to an icon button. If the icon is missed, the next move is a tooltip on first visit, not bringing the row back.
- Artifacts is OK in an overflow menu. If usage data shows it's used often enough to deserve a header-strip slot, promote it to a direct icon button between Search and the settings menu.
- `RoomArtifactsDialog`'s state machine (`open` / `onOpenChange`) is the only contract callers depend on; we did not move it because there is one writer (`setArtifactsOpen`) and one reader.

Phase 4 (320/480/768/1024/1440 breakpoint sweep + mobile verification) follows.

## Result

- 381/381 frontend tests pass; `npm run build` succeeds (existing chunk-size warning unchanged; module count drops by 1 from removing the dead `RoomSharedFilesDialog` mount).
- The chat surface gains roughly 1–2 rows of vertical space (search bar + artifacts button + their padding) on every viewport.
- Search is now reachable via `⌘K` (unchanged) and via the `RoomHeader` search icon button.
- Artifacts is now reachable via the `RoomSettingsMenu` "Artifacts" entry; the legacy 산출물 row is gone.
- `RoomSharedFilesDialog` is no longer mounted from `ChatPage`. Right-rail `FilesSection` remains the canonical shared-files entry.
- Pending: Phase 4 breakpoint sweep + mobile manual verification.

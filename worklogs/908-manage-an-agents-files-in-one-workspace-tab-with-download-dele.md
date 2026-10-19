# feat(workspace): manage an agent's files in one Workspace tab with download, delete, rename and drag-and-drop (#908)

- Commit: `0adbd19` (0adbd1903085dc109fc32d01cb51d4bef7f6ea92)
- Author: Changyong Um
- Date: 2026-10-19T19:05:37+09:00
- PR: #908

## Situation

The Workspace tab previously only allowed browsing and uploading an agent's work files. There was no way for a user to download, delete, or rename files directly from the UI, and the agent's instruction files (AGENTS.md, CLAUDE.md, skills/) appeared mixed into the work-file listing. Users who wanted to retrieve or manage existing files had to resort to workarounds outside the product.

## Task

- Add download, delete, and rename capabilities for work files in the Workspace tab, gated behind a new machine capability (`managed_workspace_files_v2`).
- Hide instruction files (AGENTS.md, CLAUDE.md, skills/) from the work-file browser root so they cannot be read or written via the workspace path; instruction changes must go through the dedicated instructions section.
- Expose download as an HTTP attachment endpoint (`GET /agents/{id}/workspace/download`) with a 413 response when the file exceeds 768 KiB.
- Add `POST …/workspace/delete` and `POST …/workspace/rename` server routes; surface `can_manage_files` in the workspace response.
- Support drag-and-drop upload of multiple files with a replace-confirmation prompt.
- Add image preview for files the UI can identify as images.
- Move the instructions/skills section from Settings into the Workspace tab so everything on the machine lives in one place.
- Cover the new operations with pytest, vitest, and Playwright tests (requirement WS-02).

## Action

**Machine layer (`packages/cluster/anygarden/machine/managed_workspace.py`)**
- Added `FILES_CAPABILITY = "managed_workspace_files_v2"` constant.
- Added `MAX_DOWNLOAD_BYTES = 768 * 1024` and `MAX_DOWNLOAD_ENCODED` limits; added `MAX_DELETE_ENTRIES = 1000`.
- Added `INSTRUCTION_NAMES = frozenset({"AGENTS.md", "CLAUDE.md", "skills"})`.
- `path_parts()`: raises `ValueError("instructions are managed separately")` when the first path segment is in `INSTRUCTION_NAMES`.
- `browse()`: extended `operation` literal to include `"fetch"`, `"delete"`, `"rename"`; `fetch` reads up to `MAX_DOWNLOAD_BYTES` bytes and stores them base64-encoded in `WorkspaceSnapshot.content_base64`; `delete` calls new `_remove_tree()` helper; `rename` calls new `_rename_entry()` helper.
- Directory listing skips `INSTRUCTION_NAMES` at the workspace root.
- `_remove_tree()`: counts all entries before removing any (refuses trees larger than `MAX_DELETE_ENTRIES`); never follows symlinks; performs recursive `unlinkat`/`rmdirat` via `os` with directory-FD discipline.
- `WorkspaceSnapshot`: new optional field `content_base64`.

**Server layer (`packages/cluster/anygarden/workspaces/managed_router.py`)**
- `ManagedWorkspaceOut`: added `can_manage_files: bool = False`.
- `_query()`: checks `FILES_CAPABILITY` in machine capabilities and gates `FILE_OPERATIONS = {"fetch", "delete", "rename"}` behind it; `RenameInput` model with `new_name` field added.
- New routes: `GET /agents/{id}/workspace/download` returns a `Content-Disposition: attachment` response (raises 413 on too-large); `POST …/workspace/delete`; `POST …/workspace/rename`.
- `EDIT_OPERATIONS` set extended with `"delete"` and `"rename"`.

**Frontend (`packages/cluster/frontend/src/components/agent-settings/`)**
- `ManagedWorkspacePanel.tsx`: added `busy`, `actionError`, `renaming`, `dragging`, `uploads`, `imageUrl` state; `canManage` flag; per-entry Download / Rename / Delete action buttons; inline rename input with save/cancel; drag-and-drop zone with depth counter; multi-file upload with replace-confirmation dialog; image preview via `URL.createObjectURL`; `run()` helper for async actions.
- New `workspaceFiles.ts`: `fetchWorkspaceFile()`, `downloadWorkspaceFile()`, `changeWorkspace()`, `uploadWorkspaceFiles()`, `isImagePath()`, `validEntryName()`.
- `AgentSettingsDialog.tsx` / `ManifestPanel.tsx`: instructions and skills sections moved from Settings dialog into the Workspace tab.
- `useManagedWorkspace.ts`: exports `WorkspaceEntry` type for use in panel.
- `i18n/catalogs/agentSetup.ts`: added ~50 new translation keys for the new UI strings.

**Tests**
- `packages/cluster/tests/machine/test_managed_workspace.py`: 80-line pytest suite covering fetch, delete (including over-limit tree), rename, and instruction-name guard.
- `packages/cluster/tests/test_managed_workspace_api.py`: 76-line pytest suite for the three new HTTP routes.
- `packages/cluster/frontend/src/components/agent-settings/ManagedWorkspacePanel.files.test.tsx`: 90-line vitest suite for download/delete/rename UI interactions.
- `packages/cluster/frontend/e2e/agent-workspace-files.spec.ts`: 88-line Playwright suite for end-to-end file management flows.

**Docs / changelog**
- `docs/requirements.md`: WS-02 marked.
- `packages/cluster/CHANGELOG.md`: entry added.

## Decisions

- **Capability flag (`managed_workspace_files_v2`) instead of always-on**: file-management operations (delete, rename) are destructive; gating them behind a capability lets older machine images opt out and allows gradual rollout. The alternative of always enabling on any machine that supports `managed_workspace_browse_v1` was rejected because it would expose delete/rename on machines not updated to handle the new frame types.
- **768 KiB download limit (one machine frame)**: the machine communicates over WebSocket frames capped at ~1 MiB after base64 overhead (same bound as room artifacts). A hard limit avoids blocking the frame pipeline on large binary files; the 413 response and `too_large` preview status tell the client to fall back to an alternate transfer path if needed. Streaming was considered but ruled out as disproportionate complexity for a management UI.
- **Count-before-delete in `_remove_tree()`**: deleting a directory tree is not atomically reversible. Counting first at `MAX_DELETE_ENTRIES = 1000` avoids partial deletes of unexpectedly large trees and gives the user a meaningful error. Alternatives (size in bytes, confirmation dialog only) were weaker—counting entries is cheaper and more predictable than statting every file for its byte size.
- **Instruction files hidden at the root, not just read-only**: showing AGENTS.md / CLAUDE.md / skills/ in the work-file list would confuse users who expect instructions to live in the dedicated Instructions section. Filtering them out of the listing and raising an error in `path_parts()` makes the separation unconditional rather than relying on UI-level guards.
- **Move instructions from Settings → Workspace tab**: having the agent's entire on-disk footprint (instructions + work files) in one place reduces context switching and matches the mental model of "everything the agent owns on this machine." The Settings dialog retains only configuration that isn't file-based.

## Result

- Users can download any work file (up to 768 KiB) as an attachment, rename files within a folder, and delete files or entire directory trees (up to 1,000 entries) directly from the Workspace tab.
- Multiple files can be dragged or picked and uploaded in one go, with per-file outcome reporting and a replace-confirmation prompt.
- Image files show an inline preview when opened.
- AGENTS.md, CLAUDE.md, and skills/ are invisible in the work-file browser and cannot be accessed via workspace paths, ensuring instruction management flows exclusively through the Instructions section.
- The Instructions and Skills sections now appear above the work-file listing in the Workspace tab.
- CI: Test (Windows native) passing; Test (Linux) and Build frontend pending at time of commit.

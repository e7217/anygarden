# feat(admin): upload/download agent manifest files from edit dialog (#98)

- Commit: `d11902b` (d11902bae8569da50c1279b83a101c3500a02e46)
- Author: Changyong Um
- Date: 2026-04-18T15:53:05+09:00
- PR: #98

## Situation

`AgentEditDialog` (admin manifest editor at `packages/cluster/frontend/src/components/AgentEditDialog.tsx`) previously supported only inline textarea editing. Admins had to copy-paste prepared skill files and engine configs one by one to get them into the `agent_files` manifest, and there was no way to export what was already stored other than selecting the textarea and copying its contents. The server-side storage is already textual (`agent_files.content` is a text column, `doorae/agent_files.py` allows only `.md/.json/.toml/.txt/.yaml/.yml/.env`), so a text-only upload/download path was a natural extension that needed no backend changes.

## Task

- Add an "Upload" control that accepts a local text file and stages its content as a new row in the dialog's working copy, reusing the existing Save pipeline (`upsertAgentFile`).
- Reject binary files before they can hit the text column; `FileReader.readAsText` silently replaces invalid bytes with U+FFFD, so a stricter decoder was required.
- Add a "Download" control for the currently selected file that writes the *working copy* (not `originalContent`) so in-progress edits survive the round-trip.
- Match the server-side whitelist in client-side validation (prefix + extension) for immediate feedback; leave path-structure checks (length, control chars, depth) to the server.
- Keep the change scoped to one frontend file (+ tests); no server/DB/API surface changes.

## Action

- `packages/cluster/frontend/src/components/AgentEditDialog.tsx`:
  - Added module-level constants `ALLOWED_EXTENSIONS` and `UPLOAD_ACCEPT`, plus helpers `decodeUtf8Strict(file)` (uses `TextDecoder('utf-8', { fatal: true })`) and `basename(path)`. Sync comment pins them to `doorae/agent_files.py`.
  - New state: `pendingContent: string | null` tracks a staged upload awaiting path confirmation; `fileInputRef` drives the hidden picker.
  - New handlers: `handleUploadClick` (opens the OS picker), `handleUploadChange` (decode + stage + prefill `newFilePath=skills/<basename>` + show confirmation form), `handleCancelNewFile` (single reset path for both manual and upload flows), `handleDownload` (Blob + `createObjectURL` + click + revoke).
  - Extended `handleAddFile` with two new behaviors while preserving the original manual-create semantics: (1) extension whitelist check; (2) upload branch that unconditionally writes content, un-deletes tombstoned rows, and prompts `window.confirm` before overwriting an existing path. Manual "New file" still rejects duplicates and preserves the prior-content-on-undelete semantics.
  - JSX: added an "Upload" button next to "New file" (`Upload`/`Download` lucide icons), the hidden `<input type="file" accept={UPLOAD_ACCEPT}>`, an "Upload" badge that appears inside the confirmation row when `pendingContent !== null`, and a Download button in the editor header line next to `selectedFile.path`.
  - Close-reset `useEffect` now also clears `pendingContent` to avoid stale upload state bleeding into the next dialog open.
- `packages/cluster/frontend/src/components/AgentEditDialog.test.tsx` (new, 148 lines): Vitest + Testing Library suite covering UTF-8 upload staging, binary rejection (`0xff 0xfe …`), `.sh` rejection via extension whitelist, and the Download blob construction. `beforeAll` stubs `URL.createObjectURL`/`revokeObjectURL` because jsdom does not implement them.

## Decisions

Design rationale was pre-recorded in `.tmp/plan-98-agent-file-upload-download.md` §3.2. Highlights:

- **Where the user sets the upload path** — options were (A) prefill a proposed path into the existing "New file" input and let the admin edit it, (B) auto-insert at `skills/<basename>`, or (C) pick a prefix first and drop the file into it. Chose A because skills live at nested paths like `skills/greeting/SKILL.md`, so "pick a prefix" (C) is insufficient and "auto-insert" (B) puts `.codex/` configs in the wrong place silently. Reusing the existing `newFilePath` Input also keeps the UX consistent between manual and upload flows.
- **UTF-8 validation** — `FileReader.readAsText`/`Blob.text()` silently substitute U+FFFD on invalid bytes, which would allow a binary file to land as corrupted text in `agent_files.content`. Chose `TextDecoder('utf-8', { fatal: true })` on the raw `ArrayBuffer` so invalid bytes throw and the upload is rejected up-front. The acceptance criterion explicitly required binary rejection, so any approach that can only "best-effort" decode was disqualified.
- **Download button placement** — options were (A) a per-row download icon next to Trash2, (B) a single button in the selected-file editor header, or (C) "Download all" as a zip. Chose B: the feature is scoped to a single file, the row is already busy with the hover-to-appear Trash2 icon, and a zip would require a new dependency outside this change's scope.
- **Overwrite on upload vs manual add** — manual "New file" continues to reject duplicates (preserves the long-standing behavior that delete+re-add is a no-op, keeping the original content). Upload is an explicit content gesture, so duplicates prompt `window.confirm` to overwrite instead of erroring. This split came up in plan §3.2 decision 4; `window.confirm` was preferred over a custom dialog for scope.
- **Client-side validator scope** — mirroring only prefix + extension (not path length, control chars, depth, `workspace/` blocks) keeps the two places in sync-manageable. The server is still authoritative and its error messages are specific enough to surface directly to the admin.

Assumption worth revisiting if violated later: uploaded files are small (single-file, typical manifest ≤ tens of KB). No explicit size limit is enforced on client or server; very large text uploads would currently round-trip through the browser's `File.arrayBuffer()` and then a PUT body.

## Result

- New admin flow: Upload button → OS picker → UTF-8 decode → confirm path → Save; working copy ends up with `dirty: true` row so existing Save semantics ship the content to the server.
- Binary uploads (`0xff`-heavy bytes, PNGs, zips) are refused before touching state; user sees a specific error message.
- Non-whitelisted extensions (e.g. `.sh`, `.py`) are rejected client-side at Add time, matching `_ALLOWED_EXTENSIONS` in `doorae/agent_files.py`.
- Download button on the editor header packages `selectedFile.content` as a UTF-8 text blob named after the path basename; works for dirty rows because it reads the working copy, not server state.
- Tests: 4 new Vitest cases pass; full frontend suite now 121/121 (was 117). `npm run build` passes tsc + vite.
- Pending: drag-and-drop, bulk zip upload/download, custom overwrite dialog (replacing `window.confirm`), and client-side size cap are all out of scope for this change and would be follow-up issues.

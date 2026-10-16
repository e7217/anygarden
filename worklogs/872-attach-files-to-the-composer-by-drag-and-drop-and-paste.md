# feat(ui): attach files to the composer by drag-and-drop and paste (#872)

- Commit: `8cd019c` (8cd019c073a7c7c9c5c38f947151390779c2de17)
- Author: Changyong Um
- Date: 2026-10-16T17:25:27+09:00
- PR: #872

## Situation

The composer previously allowed only single-file attachment via a file picker (`<input type="file">`). Drag-and-drop and paste were not wired up, so users working with screenshots or files from other apps had to go through the picker every time. Additionally, `uploadRoomFile` threw a generic `Error` with the raw JSON body on failure, making it impossible for callers to distinguish HTTP 413 (too large) from 415 (unsupported type) from other errors.

## Task

- Wire drag-and-drop into the composer column so a drop zone hint appears and files are attached.
- Handle clipboard paste: files in the clipboard should attach instead of pasting text; screenshots named `image.png` must get unique paste-time names to avoid upsert races.
- Extend the file picker to accept multiple files at once (`multiple` attribute).
- All three code paths must share one sequential upload function (parallel uploads of same-name files would race the server's upsert logic).
- Replace the raw-JSON error string with a typed `RoomFileUploadError` carrying the HTTP status so UI can map 413/415 to human-readable i18n strings.
- Cover the new flows with Vitest unit tests and a Playwright e2e spec.

## Action

**`packages/cluster/frontend/src/lib/roomFiles.ts`**
- Introduced `RoomFileUploadError extends Error` with a `status: number` field (`roomFiles.ts:27-37`).
- `uploadRoomFile` now `throw new RoomFileUploadError(resp.status, await resp.text())` instead of the previous plain `throw new Error(detail)` (`roomFiles.ts:51`).

**`packages/cluster/frontend/src/components/MessageInput.tsx`**
- Added helper `uploadFailureReason(err)` that maps HTTP status to an i18n `MessageKey` (`MessageInput.tsx:60-65`).
- Added `namePastedFile(file, now)` to rename generic `image.png` screenshots to `pasted-YYYYMMDD-HHmmss.png` (`MessageInput.tsx:67-79`).
- Added `carriesFiles(types)` predicate to filter drag events that carry no files (`MessageInput.tsx:82-83`).
- Replaced `uploading/uploadError` state pair with `pendingUploads: number` counter + `uploadFailures: UploadFailure[]` array — supports concurrent files while still displaying per-file failure details (`MessageInput.tsx:172-179`).
- Introduced `dragDepth: useRef(0)` to count `dragenter`/`dragleave` events and prevent the hint from flickering when the pointer crosses child elements (`MessageInput.tsx:181`).
- New central `uploadFiles(files)` — iterates files sequentially with `for…of`, appending each success to `attachments` and each failure to `uploadFailures` (`MessageInput.tsx:397-427`).
- `handleFileSelected` delegates to `uploadFiles` and the picker now has `multiple` + `data-testid="composer-file-input"` (`MessageInput.tsx:429-438`).
- Added `handlePaste` — grabs `clipboardData.files`, calls `namePastedFile` for each, delegates to `uploadFiles` (`MessageInput.tsx:440-447`).
- Added `handleDragEnter`, `handleDragOver`, `handleDragLeave`, `handleDrop` managing `dragDepth` and `dragActive` state (`MessageInput.tsx:449-477`).
- Composer column now mounts all four drag handlers; overlays a dashed drop hint when `dragActive` is true (`MessageInput.tsx:560-581`).
- `uploadFailures` rendered as `<span role="alert">` chips showing `{name} — {reason}` instead of the old single `uploadError` string (`MessageInput.tsx:607-615`).

**`packages/cluster/frontend/src/i18n/catalogs/chat.ts`**
- Added `chat.dropFilesHere` in EN (`'Drop files to attach'`) and KO (`'여기에 놓아 첨부'`).

**`packages/cluster/frontend/src/components/MessageInput.test.tsx`**
- New `describe` block with 6 unit tests covering: multi-file drop, drag with no files (ignored), file paste, text-only paste (passes through), screenshot rename, per-file error reporting with fallback to other files.

**`packages/cluster/frontend/e2e/composer-attachments.spec.ts`** (new file, 174 lines)
- Playwright e2e spec running against Vite with REST + WebSocket stubbed; tests dropping two files and pasting a screenshot with a timestamped rename.

## Decisions

**Sequential vs parallel uploads**: same-name re-uploads upsert the room file on the server. Uploading two `image.png` pastes in parallel would race and one would silently win. Sequential `for…of` avoids this at negligible UX cost for typical attachment counts. Alternative — UUID-prefix every upload client-side — was rejected because it pollutes the room file list with duplicates on retry.

**`dragDepth` counter vs `relatedTarget` check**: the `relatedTarget` check for `dragleave` is unreliable across browsers when the cursor moves over children. A depth counter incremented on `dragenter` and decremented on `dragleave` is the standard workaround and avoids any browser-detection logic.

**`RoomFileUploadError` typed class vs plain object**: a plain `{ status, message }` object was considered, but `instanceof` checks on a named class are cleaner in the `uploadFailureReason` mapper and survive bundler transforms that strip TypeScript interfaces. The class also gives `.message` for free, useful for logging.

**Per-file failure UI vs single error banner**: the old single `uploadError` string was replaced with a list of `UploadFailure` chips so users can see exactly which file failed (e.g. `photo.png — file type not allowed`) while successfully attached files remain visible. A modal or toast was considered but adds more surface area; inline chips keep failures co-located with attachments.

**Assumption**: server upsert semantics (same name → replace) are stable. If the server later changes to reject duplicate names, the sequential strategy would need revisiting to add UUID prefixes.

## Result

- Users can attach files by dragging onto the composer, pasting from clipboard, or picking multiple files at once via the picker.
- Pasted screenshots get unique names (`pasted-YYYYMMDD-HHmmss.png`) to prevent silent overwrites.
- Upload failures show per-file human-readable reasons (`file type not allowed`, `file too large`, `upload failed`) instead of raw server JSON.
- 6 new Vitest unit tests and 2 new Playwright e2e tests pass, covering drop, paste (file and text), screenshot rename, partial failure, and multi-pick.
- Pending: issue #873 will lift the server-side image MIME restriction (currently returning 415 for images, verified in e2e stub).

# feat(ui): attach images and PDFs to your own messages (#873)

- Commit: `1049481` (10494814286bc23b7e70f718d704cca88e8edd76)
- Author: Changyong Um
- Date: 2026-10-16T18:07:51+09:00
- PR: #873

## Situation

The earlier file-attachment work (#872) wired drag-and-drop and paste into the composer but routed every file through the room's shared-file store, which is designed for text artifacts. Binary formats — images (PNG, JPEG, GIF, WebP) and PDFs — belong in the room-artifact store, which tracks mime type and serves blobs directly. Additionally, a person's own message bubble did not render its `artifact` references visually; they appeared only on agent replies.

## Task

- Route PNG, JPEG, GIF, WebP, and PDF uploads to `POST /api/v1/rooms/:id/artifacts` instead of the shared-file endpoint.
- Reject SVG before upload (SVG can carry script when downloaded and opened).
- Show a local image thumbnail in the attachment chip while the file is pending/sent.
- Release all blob URLs on chip removal and on message send to avoid memory leaks.
- Render `artifact` references inside a person's own right-aligned message bubble, using the same `MessageAttachments` component already used for agent replies.

## Action

- **`packages/cluster/frontend/src/lib/roomArtifacts.ts`**: Added `uploadRoomArtifact(roomId, file)` — POSTs a `FormData` body to the artifacts endpoint, reuses `RoomFileUploadError` for non-ok responses.
- **`packages/cluster/frontend/src/components/MessageInput.tsx`**:
  - Split `Attachment` into a discriminated union: `{ kind: 'shared_file', … }` | `{ kind: 'artifact', id, filename, previewUrl? }`.
  - Added `ARTIFACT_TYPES`, `ARTIFACT_MIMES`, and `attachmentStore(file)` to classify each file by mime or extension; returns `null` for SVG (triggers HTTP 415 error path).
  - `addAttachment` dedupes by `(kind, id)` and releases the blob URL on a duplicate.
  - `removeAttachment` now takes the full `Attachment` object and calls `releasePreview`.
  - `releasePreview` calls `URL.revokeObjectURL` only for artifact chips with a `previewUrl`.
  - On send: revokes all remaining blob URLs before clearing the `attachments` array.
  - Unmount effect via `attachmentsRef` releases any blob URLs still live.
  - Chip rendering: shows an `<img>` thumbnail for artifact images; falls back to the paperclip icon for PDFs and text files.
  - References payload: artifact attachments append `{ type: 'artifact', id }` entries; shared-file entries are unchanged.
- **`packages/cluster/frontend/src/components/MessageAttachments.tsx`**: Added `align?: 'start' | 'end'` prop (default `'start'`); the container switches to `items-end` when `align="end"`.
- **`packages/cluster/frontend/src/components/MessageBubble.tsx`**: Added `<MessageAttachments metadata={message.metadata} align="end" />` inside the person's own bubble branch (line 547).
- **`packages/cluster/frontend/e2e/composer-attachments.spec.ts`**: Extended E2E suite with scenarios covering image/PDF chip rendering, thumbnail presence, SVG rejection, and artifact reference inclusion in the sent message payload.
- **`packages/cluster/frontend/src/components/MessageInput.test.tsx`** and **`MessageBubble.test.tsx`**: Unit tests for the new attachment kind, dedup logic, blob URL cleanup, and bubble rendering.

## Decisions

- **Room artifact store vs. shared-file store for binaries**: The shared-file store stores structured text (JSON metadata + storage name + sha256). The artifact store stores the raw blob and tracks mime type, making it the natural home for images and PDFs. Routing binaries there avoids encoding issues and lets the server serve them directly.
- **SVG exclusion**: SVG files can embed `<script>` and execute JavaScript when opened in a browser. Rejecting them before upload (returning `null` from `attachmentStore`) is the safest default; it mirrors common CDN upload policies. The exclusion is documented in the type comment so future reviewers know it is intentional, not an oversight.
- **Blob URL lifecycle via `releasePreview` + `attachmentsRef`**: An alternative was to store blob URLs in a separate ref map. A discriminated union field (`previewUrl`) keeps the URL co-located with the attachment it belongs to, making revocation straightforward. The `attachmentsRef` trick (a ref that mirrors state) is needed to access the latest array inside the unmount `useEffect` without capturing a stale closure.
- **`align` prop on `MessageAttachments`**: The simplest extension — adding one prop — avoids duplicating the component or wrapping it in a layout container at every call site. The default `'start'` preserves all existing agent-reply behaviour without change.

## Result

- PNG, JPEG, GIF, WebP, and PDF files attached in the composer are uploaded to the artifact store and included as `{ type: 'artifact', id }` references in the sent message.
- SVG files are rejected with an unsupported-type error before any upload attempt.
- Attached image chips show a local thumbnail; PDF chips show the paperclip icon.
- All blob URLs are revoked when a chip is removed, when the message is sent, or when the composer unmounts, preventing memory leaks.
- A person's own message bubble now renders its artifact attachments (images inline, PDFs as download cards) aligned to the right-hand end of the bubble, matching the agent-reply display.
- E2E and unit tests cover the new paths; existing attachment tests continue to pass.

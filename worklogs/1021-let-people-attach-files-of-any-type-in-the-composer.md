# feat(chat): let people attach files of any type in the composer (#1021)

- Commit: `2f492f2` (2f492f2c2758768b709e2a0afdf9cb29b78664ba)
- Author: Changyong Um
- Date: 2026-11-11T16:54:23+09:00
- PR: #1021

## Situation

The composer's file attachment logic (introduced in #873) only accepted a fixed allow-list of MIME types — images (PNG, JPEG, GIF, WebP) and PDF — and explicitly rejected SVG, executables, office files, and anything else. The server mirrored this restriction with an `ArtifactMimeNotAllowedError` that returned HTTP 415 for person-uploaded types outside the allow-list. Meanwhile agents could already send any type (#1017), and #1017 also added `Content-Disposition: attachment` + `X-Content-Type-Options: nosniff` + CSP sandbox headers to all artifact downloads, making the browser-safety rationale for the allow-list obsolete.

## Task

- Remove the person-upload MIME allow-list from the server (`ArtifactMimeNotAllowedError`, its call-site in `store_uploaded_artifact`, and the 415 branch in the router).
- Remove the client-side `accept=` filter from the file input so the native file picker no longer hides file types.
- Remove SVG's special "refused" path in the frontend; SVG, zip, exe, pptx, and any binary now route through `uploadRoomArtifact`.
- Keep small text files (≤ 256 KiB) going to the room's shared files so agents pick them up as context; route everything else — including large text — as a room artifact to the agent inbox.
- Raise the agent inbox file size cap from 10 MiB to 20 MiB to match the server's default upload limit, so a file a person can attach will always reach the agent.
- Update requirement MSG-11 and all related tests.

## Action

- **`packages/cluster/anygarden/rooms/artifacts.py`**: deleted `ArtifactMimeNotAllowedError` class; removed the `if agent_id is None and mime not in ALLOWED_MIME_TYPES: raise` guard in `store_uploaded_artifact`; updated the docstring to reflect that both agents and people may attach any type.
- **`packages/cluster/anygarden/rooms/router.py`** (`upload_room_artifact`): collapsed the two-branch mime-fallback condition into a single `if mime == OCTET_STREAM` check; removed the `except ArtifactMimeNotAllowedError` handler.
- **`packages/cluster/anygarden/agent/runtime/attachments.py`**: raised `INBOX_MAX_BYTES` from `10 * 1024 * 1024` to `20 * 1024 * 1024` with an explanatory comment referencing #1021.
- **`packages/cluster/frontend/src/components/MessageInput.tsx`**:
  - Deleted `ARTIFACT_TYPES`, `ARTIFACT_MIMES`, `FILE_ACCEPT`, `TEXT_ACCEPT` constants.
  - Added `SHARED_FILE_MAX_BYTES = 256 * 1024`, `TEXT_EXTENSIONS`, `TEXT_MIMES` to describe what qualifies as a shared file.
  - Rewrote `attachmentStore`: now returns only `'artifact' | 'shared_file'` (never `null`); returns `'shared_file'` only for MIME/extension-detected text files at or below 256 KiB, everything else is `'artifact'`.
  - Removed the `store === null → throw RoomFileUploadError(415)` branch from the upload loop.
  - Removed `accept={FILE_ACCEPT}` from the `<input type="file">` element.
- **`packages/cluster/frontend/src/lib/roomArtifacts.ts`**: updated the JSDoc comment.
- **`packages/cluster/frontend/src/components/MessageInput.test.tsx`**:
  - Replaced `refuses SVG without uploading it` with `sends any non-text file as an artifact` covering pptx, zip, svg, and a binary blob.
  - Added `sends text too large for a shared file as an artifact` for a 300 KiB CSV.
  - Replaced `lets the file picker offer images and PDFs` with `lets the file picker offer every file type` asserting `accept === ''`.
- **`packages/cluster/tests/test_rooms_artifacts_endpoints.py`**: replaced `test_human_upload_keeps_the_allow_list` (which expected 415 for `.exe`) with `test_human_may_upload_any_type` (expects 201 for pptx, exe, svg with correct MIME echoed back and CSP sandbox header on download); added `test_missing_type_is_guessed_from_the_filename`.
- **`docs/requirements.md`**: updated MSG-11 wording to reflect the new routing rule and the raised inbox cap.

## Decisions

- **Drop the MIME allow-list entirely rather than extending it**: the original list was a browser-safety measure. Once #1017 ensured that all artifact downloads carry `Content-Disposition: attachment`, `X-Content-Type-Options: nosniff`, and `Content-Security-Policy: sandbox allow-scripts`, the browser is protected regardless of file type. Maintaining an ever-growing allow-list added friction for users (SVG logos, office files, executables) with no remaining security gain. Alternative — keep the list but add more types — was rejected because it is an indefinitely growing maintenance surface with no clear stopping criterion.
- **Route text ≤ 256 KiB to shared files, everything else to artifacts**: shared files are the existing mechanism (#246) agents use as ambient context; forcing large or binary files through that path would bloat context windows. The 256 KiB threshold is the existing shared-file size limit; using it as the split point required no new infrastructure. Alternative — route all person uploads as artifacts — was considered but would have broken the useful "small text as context" behaviour for small code snippets and notes.
- **Raise inbox cap to 20 MiB (from 10 MiB)**: the server's `artifact_upload_max_bytes` default is 20 MiB. If a person can successfully upload a 15 MiB file and attach it to a message, the agent receiving that message would have silently ignored it under the old 10 MiB cap. The fix aligns the two limits. Assumption: if the server's upload cap is later raised above 20 MiB, `INBOX_MAX_BYTES` will need a corresponding update.
- **SVG treated as non-text (routed to artifact)**:  `mime !== 'image/svg+xml'` is explicitly excluded from the `isText` check even though SVG is XML-based text. SVG files can carry inline script; routing them as artifacts means the download receives the CSP sandbox header, which is safer than storing them as shared-file text that an agent might embed verbatim into a reply.

## Result

- People can attach any file type from the composer (SVG, zip, exe, pptx, binary blobs, etc.); the browser file picker no longer filters types.
- Small text files (≤ 256 KiB) still reach agents as shared-file context; all other attachments go to the agent inbox as room artifacts.
- The agent inbox now accepts files up to 20 MiB, matching the server upload cap.
- All new behaviour is covered by updated vitest and pytest tests under `MSG-11`.
- No server-side allow-list to maintain going forward; download safety is handled uniformly by `download_headers` for all artifact types.

# feat(chat): attach files an agent writes during a turn to its reply (#854)

- Commit: `f4248fb` (f4248fba0190340e3cda3d3c70831cf9cc749b76)
- Author: Changyong Um
- Date: 2026-10-16T12:52:22+09:00
- PR: #854 (issue)

## Situation

Files an agent wrote to `memory/outbox/<room_id>/` were polled by the machine daemon, shipped as a single `room_artifact_produced` WS frame and shown only in the room's artifact dialog. Nothing tied a file to the reply that produced it, so users could not see the file next to the answer. The frame envelope capped files at 768 KiB, and PDF was not allowed. Separately, `metadata.references` entries other than `shared_file` were stored without any validation.

## Task

- Show files an agent creates during a turn inside the chat, attached to that turn's reply: images inline, other types as download cards.
- Raise the size cap and allow PDF without breaking the existing outbox → artifact dialog path.
- Keep a failed attachment from blocking the reply, and tell the user what was not attached.
- Stop forged message metadata from surfacing another room's or another agent's file.

## Action

- Server upload: `POST /api/v1/rooms/{room_id}/artifacts` in `packages/cluster/anygarden/rooms/router.py` accepts uploads from agent participants only (`MESSAGE_SEND`). It enforces `artifact_upload_max_bytes` (20 MiB, `config.py`) and returns 201, or 200 with the existing row when the room already has the same sha256.
- `rooms/artifacts.py`: the insert, disk write and rollback logic moved into `_insert_artifact`, which the daemon frame path and the new `store_uploaded_artifact` now share. Added `artifact_payload` / `broadcast_artifact_added` (also used by `ws/machine_handler.py`) and `application/pdf` (also added to the daemon whitelist).
- `messages/references.py`: `canonicalize_shared_file_references` became `canonicalize_message_references(sender_agent_id=...)`. It rebuilds `artifact` references from `room_artifacts` (same room, produced by the sender), drops artifact references from non-agents, and drops unknown reference types. Callers in `ws/handler.py` and `messages/router.py` pass the sending agent's id.
- Runtime: new `agent/runtime/attachments.py` provides `snapshot_outbox`, `changed_outbox_files` and `upload_reply_attachments` (limit 10, failure reasons `too_large` / `unsupported_type` / `too_many` / `upload_failed`). `ChatClient.upload_room_artifact` sends a multipart request with a 60s timeout and raises `ArtifactUploadError(status)`. `RoomHandlerSupervisor(outbox_dir=...)` snapshots the outbox at turn start and, on the successful-reply path only, merges `references` and `undelivered_attachments` into the reply metadata. `register_room_adapter` passes `Path.cwd()/memory/outbox`.
- Agent instructions (`machine/spawner.py`): describe reply attachments, the 10-file / 20 MiB limits and PDF, and tell agents to keep drafts out of the outbox.
- Web: `MessageAttachments.tsx` shows image thumbnails (a lightbox dialog with a download button), file cards, an "unavailable" state when the file is gone, and warning chips for undelivered files. It is wired into the agent/other-participant `MessageBubble`. Shared pieces: `ArtifactImage.tsx` (`useArtifactBlobUrl`, reused by `RoomArtifactsDialog`), `lib/messageAttachments.ts`, and `formatBytes` / `isImageMime` in `lib/roomArtifacts.ts`. ko/en strings live in `i18n/catalogs/chat.ts`.

## Decisions

- **How an agent designates attachments.** The options were: automatically attach outbox changes made during the turn, inline markers in the reply (`![](outbox:x.png)`), or an MCP `attach_file` tool. Automatic attachment won (user decision). The outbox is already defined as "files to show the user", and markers depend on each engine's LLM following a format. The MCP tool was rejected because MCP tools execute on the server and cannot read the agent's local files.
- **Transport.** The options were: an HTTP upload by the runtime before sending the reply, keeping the daemon frame and referencing by sha256, or chunked daemon frames. HTTP won because only the runtime knows turn boundaries and it already calls REST with its bearer token. The sha256-only option was rejected because the reply can arrive before the polled file, and files over 768 KiB would never arrive, leaving a broken reference.
- **Storage.** The options were: `metadata.references` with `type: "artifact"`, a `room_artifacts.message_id` column, or a new table. Metadata won: it mirrors `shared_file`, needs no migration, and message deletion already strips references. The trade-off is that deleting an artifact leaves a reference, which the UI renders as "unavailable".
- **Change detection.** Compare sha256 against a snapshot instead of using mtime, because mtime can be preserved or coarse.
- **Revisit if:** the agent runtime's cwd stops being the agent root (for example, on remote or federated nodes), or reply attachments need versions, comments or public links (Raft Artifacts–style scope, explicitly excluded).

## Result

- An agent reply now carries the files it wrote during the turn. In a live check on a real server with `ChatClient` and the supervisor, two PNGs rendered inline, the PDF rendered as a card, the lightbox and download worked, and `tool.exe` showed a "not attached (type not allowed)" chip. Checked at 1440px light and 375px dark with no horizontal overflow.
- Tests added: upload endpoint (9), artifact reference canonicalization (7), runtime snapshot/upload/supervisor (9), outbox instructions (1), frontend `MessageAttachments` (5) plus a `MessageBubble` case.
- Full suites: cluster pytest 3835 passed / 7 skipped; frontend vitest 919 passed; `npm run build` OK.
- Not yet verified: a turn driven by a real LLM engine (codex/pi) on a live node.

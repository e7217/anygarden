# feat(workspace): transfer large work files and folder zips over HTTP with one-time tickets (#914)

- Commit: `bcd40c4` (bcd40c440c848eb6815f0af1dc416e92ddfab47f)
- Author: Changyong Um
- Date: 2026-10-19T20:20:53+09:00
- PR: #914

## Situation

Work-file downloads and uploads previously travelled through a single machine WebSocket frame, which hard-capped downloads at 768 KiB and uploads at 1 MiB with no support for folder downloads at all. Any file exceeding those limits was simply inaccessible from the browser UI. The constraint lived in the WebSocket framing layer and could not be relaxed without an out-of-band data channel.

## Task

- Replace the in-frame transfer path with an HTTP sidecar channel for files larger than the WebSocket limit (requirement WS-07).
- Support folder download as a zip archive.
- Keep the channel secure: the integrated node's machine has no server-issued machine token, so the ticket must be self-contained and time-limited.
- Fix a concurrent instruction-sync race introduced in #910 where a rejected second update could install an older copy over a newer agent edit.
- Avoid bloating the database: store only the token hash; clean up spool files deterministically.

## Action

**New server-side transfer model** (`packages/cluster/anygarden/workspaces/transfers.py`):
- `issue()` mints a one-time ticket (`MachineTransfer` row, migration 096): token hash only, bound to machine, agent, path, direction, 2-minute TTL.
- `claim()` performs HMAC-safe hash comparison, checks state == `"issued"` and expiry before marking the row in-use.
- `write_spool()` streams the PUT body into `transfer_spool_dir`; raises `SpoolTooLarge` if size exceeds `max_bytes`.
- `discard()` removes the spool file and deletes the row; called as a Starlette `BackgroundTask` after the browser response completes.

**Transfer router** (`packages/cluster/anygarden/workspaces/transfer_router.py`):
- `PUT /api/v1/machine-transfers/{ticket_id}` — machine uploads bytes; authenticated by `Authorization: Ticket <token>` alone.
- `GET /api/v1/machine-transfers/{ticket_id}` — machine downloads a previously spooled browser upload.

**Managed workspace router** (`packages/cluster/anygarden/workspaces/managed_router.py`):
- `GET …/workspace/download-stream` — single-file download up to 20 MiB.
- `GET …/workspace/archive` — folder zip up to 200 MiB / 10 000 files.
- `PUT …/workspace/upload-stream` — raw-body upload up to 20 MiB.
- `_transfer_placement()` guards: agent placed, machine online, capability present.
- `_run_transfer()` sends a `workspace_transfer_request` WebSocket frame to the machine, waits for completion (with adaptive timeout: 30 s base + 10 s/MiB, capped at 600 s).
- Workspace response gains `can_transfer`, `transfer_max_bytes`, `archive_max_bytes` when the machine advertises `managed_workspace_transfer_v1`.

**Machine side** (`packages/cluster/machine/workspace_transfer.py`, `machine_handler.py`):
- Capability `managed_workspace_transfer_v1` registered in `machine_handler.py`.
- `open_workspace` / `open_for_send` / `receive_into` / `build_archive`: descriptor-walk skips private, link, and instruction names; enforces entry and byte caps.
- `workspace_transfer_request` runs in a background asyncio task; machine PUTs or GETs `{server}/api/v1/machine-transfers/{id}` with `Authorization: Ticket <token>` and replies over the WebSocket.

**Frontend** (`ManagedWorkspacePanel.tsx`, `workspaceFiles.ts`, `agentSetup.ts`):
- Uploads > 1 MiB use XHR with progress events; downloads stream with progress.
- Folder and current-folder download as zip.
- Machines without the capability show an upgrade notice.

**Instruction-sync race fix** (`tests/test_instructions_e2e.py`, `tests/machine/test_daemon_instructions.py`):
- Only one update is in flight at a time; a rejection matching the disk hash updates only the receipt rather than reverting the agent's newer edit.
- Two end-to-end tests switched from the in-memory `StaticPool` to a file database to prevent concurrent-session write collisions.

**Tests added**: `test_workspace_transfers.py` (server), `test_managed_workspace_api.py`, `test_workspace_transfer.py` (machine), `agent-workspace-transfers.spec.ts` (e2e), `ManagedWorkspacePanel.files.test.tsx` (unit).

## Decisions

- **HTTP sidecar over extending the WebSocket frame size**: WebSocket frames travel through the same multiplexed connection used for all control messages; enlarging the limit risks head-of-line blocking and makes back-pressure harder to reason about. A separate HTTP PUT/GET route avoids that entirely and lets the browser and machine use standard streaming APIs.

- **One-time ticket with only the hash stored**: the ticket token travels inside the authenticated machine WebSocket (never in a URL), so interception is limited to that channel already protected by TLS + machine auth. Storing only the hash means a database leak does not expose usable tokens. Single-use prevents replay. 2-minute TTL bounds the window if a token leaks.

- **Ticket-only auth for the transfer endpoint** (no machine token required): the integrated node's machine process has no server-issued machine token, so attaching one would require a separate provisioning step. Since the token is delivered through the already-authenticated WebSocket, it carries the same trust level.

- **Spool-then-stream** (upload path): the browser uploads to the server first; the machine GETs from the spool. An alternative — the browser sending bytes directly to the machine — would require the machine to have a publicly reachable address, which is not guaranteed. The spool adds latency but works universally.

- **Orphan sweeper (writes only when there is something to remove)**: avoids unnecessary I/O on quiet deployments. Stale tickets and spool files are cleaned up passively rather than requiring a cron job.

## Result

- Files larger than 768 KiB can now be downloaded from the workspace panel; the limit is 20 MiB per file and 200 MiB / 10 000 entries per folder zip.
- Uploads are no longer capped at 1 MiB; the limit is now 20 MiB.
- Folder download as zip is available for the first time.
- The instruction-sync race (older copy overwriting a newer agent edit on a concurrent update) is fixed; two previously flaky e2e tests are stabilised.
- Deployment note: the reverse proxy's `client_max_body_size` (nginx) or equivalent must be raised to at least 200 MiB to allow large zip uploads.

# feat(agent): download message attachments into the agent inbox (#874)

- Commit: `beec899` (beec8997ec0679c60d93dfaa25580944208f2339)
- Author: Changyong Um
- Date: 2026-10-16T18:14:19+09:00
- PR: #874

## Situation

Agents could already attach files to their replies (#854 outbox), but files
that a human attached to an incoming message were invisible to the engine.
Because Codex CLI works from the filesystem, it needed the attached files
saved to disk before the turn prompt was assembled. On a remote machine there
was no path at all: the WS bus carries only text and caps at 1 MiB, so binary
attachments never reached the agent.

## Task

- Before each engine turn, fetch every `artifact` reference in the message's
  metadata into `memory/inbox/<room_id>/<id8>-<name>` on the agent's machine.
- Inject a `<attachments>` block into the turn prompt listing each file's
  local path (or the delivery failure reason).
- Enforce safety limits: ≤10 files per message, ≤10 MiB each, ≤100 MiB per
  room before pruning oldest files.
- Guard against path-traversal: room ids must be UUIDs; symlinked room
  directories are refused; filenames are reduced to a safe basename.
- Bridge `workspace/memory/inbox` in the Codex spawner alongside `shared/`
  and `outbox/` so codex, which runs under `workspace/`, sees the files.

## Action

- **`packages/cluster/anygarden/agent/client.py`** — added
  `ArtifactDownloadError` exception and `ChatClient.download_room_artifact()`
  which streams `GET /api/v1/rooms/{room_id}/artifacts/{artifact_id}` to a
  `.part` temp file (renamed on success), raising on non-200 or body overflow.
  The WS base URL's `ws://`/`wss://` scheme is converted to `http://`/`https://`
  before the HTTP request.

- **`packages/cluster/anygarden/agent/runtime/attachments.py`** — extended the
  module (previously outbox-only) with the inbox side: `InboxAttachment`
  dataclass, helpers `_safe_name`, `_inbox_filename`, `_room_id_is_uuid`,
  `_room_inbox`, `_artifact_references`, `_matches` (sha256 idempotency), `_prune`
  (LRU room-cap eviction), and the main `materialize_inbox_attachments()` async
  function that coordinates them all.

- **`packages/cluster/anygarden/agent/integrations/base.py`** — added
  `compose_inbox_attachments_hint()` which formats the `<attachments>` XML block
  injected into the turn prompt; extended `EngineAdapter.assemble_user_content()`
  to accept `inbox_attachments` and append the hint.

- **`packages/cluster/anygarden/agent/integrations/codex_cli.py`** — in
  `CodexCliAdapter`'s turn handler, calls `materialize_inbox_attachments()` before
  `assemble_user_content()` and passes the result as `inbox_attachments`.

- **`packages/cluster/anygarden/machine/spawner.py`** — creates `memory/inbox/`
  with `0o700` alongside `outbox/`; adds `"inbox"` to the three symlinks bridged
  into `workspace/memory/` (`shared`, `outbox`, `inbox`).

- **`packages/cluster/tests/agent/test_inbox_attachments.py`** — 282-line new
  test file covering: happy-path download, idempotent re-fetch (sha256 match),
  per-file size cap, per-message count cap, missing `download_room_artifact`,
  room-id validation, symlink room dir refusal, `_prune` cap logic, and the
  `compose_inbox_attachments_hint` formatter.

- **`packages/cluster/tests/machine/test_materialize.py`** — added assertions
  that the spawner creates the `inbox` directory and the workspace symlink.

## Decisions

**HTTP pull vs. bus push**: The WS machine bus carries only text frames and is
capped at 1 MiB, making it unsuitable for binary files. The runtime already
speaks HTTP to the same server with the same credentials for reply attachments
(#854), so extending `ChatClient` with a streaming GET reused existing auth and
connection infrastructure without a new transport.

**MCP pull tool rejected**: An MCP server runs on the cluster side and cannot
write files to a remote machine's disk, so an MCP-based approach would have
required a new protocol path with no reuse benefit.

**Temp-file + rename for atomicity**: Downloads go to `.{name}.part` and are
renamed into place only when complete. This prevents the engine from opening a
partial file if a turn races with a slow download or a process kill.

**Idempotent refetch via sha256**: If the target file already exists and its
sha256 matches the metadata reference, no download is issued. This makes retries
and reconnects cheap without a separate cache database.

**UUID-gated room ids + symlink refusal**: Inbox directories are named by room
id; allowing arbitrary strings would enable path traversal. UUIDs are
structurally constrained to be filesystem-safe and already the canonical form
used everywhere. Symlinked room directories are rejected to prevent writes
escaping the agent directory.

## Result

- Agents receive binary attachments (images, files) from incoming messages as
  local paths before the engine turn starts; a spike confirmed codex-cli 0.162.1
  reads an image and describes it correctly.
- Undeliverable files are listed with a reason (`too_large`, `too_many`,
  `unavailable`, `download_failed`) so the engine is never silently missing a file.
- A room's inbox is automatically pruned to ≤100 MiB by evicting oldest files,
  keeping disk usage bounded without manual cleanup.
- 282 new tests; spawner test extended to cover the new inbox symlink.
- Only `CodexCliAdapter` wires up the inbox for now; other engine adapters
  (`ClaudeAdapter`, etc.) can opt in by passing `inbox_attachments` to
  `assemble_user_content()`.

# Design 13 — Room shared files (#246)

> Status: MVP shipped 2026-04-23

## Purpose

Give a user a way to attach a file to a room and have every participating
agent "see" it while discussing, without rebuilding the chat message model
around mixed content types. The attachment is copy-distributed to each
agent's private `memory/shared/` directory and injected into the engine's
system prompt via a new `<shared-context>` block next to the existing
per-agent `<memory>` block (#237).

## Data flow

```
[client] MessageInput file picker
    └─ multipart POST /api/v1/rooms/{room_id}/files
          └─ [server] rooms.shared_files.upload_file
                ├─ size + MIME whitelist check
                ├─ save to tmp path, sha256, atomic rename
                │    (~/.anygarden/room_files/<room_id>/.tmp/<id> → .../<id>)
                ├─ DB upsert on (room_id, storage_name)
                │    (commit failure unlinks the freshly-written file)
                └─ BackgroundTasks: fan_out_write
                      └─ for each placed agent in the room, send
                         AgentMemorySharedFileWriteFrame over MachineBus
                         └─ [machine] daemon writes into
                            <agent_root>/memory/shared/<storage_name>
                            (skipped when on-disk sha256 already matches)
```

Delete inverts the flow: the DB row + on-disk file are removed synchronously;
`fan_out_delete` schedules an `AgentMemorySharedFileDeleteFrame` to every
placed agent so their copies go with it.

## Message references

Users can explicitly reference a room shared file from the composer with
`$filename`. The client resolves the token against the room's shared-file
list and sends a `metadata.references[]` item of type `shared_file`; directly
typed tokens and newly uploaded attachments are deduped before send.

The WebSocket handler canonicalizes each shared-file reference against the
current room before storing the message, replacing client-provided names with
DB values and rejecting cross-room or unknown file ids. Guests cannot submit
shared-file references.

Agents receive a small turn-local hint instead of another content copy:

```
<referenced-files>
- spec.md: memory/shared/spec.md
</referenced-files>
```

The referenced file content itself remains in the existing `memory/shared/`
fan-out path and `<shared-context>` prompt block.

## Storage layout

```
~/.anygarden/
├── anygarden.db
├── agents/<agent_id>/
│   └── memory/
│       ├── notes.md        # #237 — agent↔server bidirectional sync
│       └── shared/         # #246 — server→machine one-way push
│           ├── spec.md
│           └── data.json
└── room_files/
    └── <room_id>/
        ├── .tmp/           # swept on boot via cleanup_orphans
        └── <file_id>       # uuid; never exposes user filenames
```

*The DB keeps only metadata + `sha256`; the raw bytes live on disk so the
default SQLite `anygarden.db` stays compact as rooms accumulate attachments.*

## Limits & validation

| Knob | Default | Config |
|---|---|---|
| Max upload size | 256 KB | `shared_files.DEFAULT_MAX_SIZE_BYTES` |
| MIME whitelist | text/plain, text/markdown, text/csv, text/yaml, text/html, application/json, application/xml, text/x-python, … | `ALLOWED_MIME_TYPES` |
| Storage root | `~/.anygarden/room_files` | `ANYGARDEN_ROOM_FILES_DIR` |

Filename sanitisation drops path separators, control chars, reserved names
(`.`, `..`, ``) and truncates to 200 bytes. On-disk filename is always a
uuid — user input never reaches the filesystem path.

## Access control

- Upload / list / delete: any participant of the room (+ global admin).
- Guests are rejected up-front regardless of room scope.
- File content is never served over HTTP to clients in this MVP — the
  REST endpoints only return metadata. The content is delivered to agents
  via machine frames.

## Failure modes

| Mode | Outcome |
|---|---|
| Disk write succeeds, DB commit fails | File on disk is unlinked immediately. Startup `cleanup_orphans` sweeps anything that slipped through. |
| Machine offline at upload time | Write frame dropped at `MachineBus.send`. On reconnect, `_schedule_shared_files_backfill` (new agent join) or a future machine-reconnect hook re-delivers. Frames are idempotent — same `sha256` short-circuits on the daemon side. |
| Agent process running when new file arrives | File lands on disk under `memory/shared/` immediately; the agent picks it up on the **next** prompt composition (session boundary). Live injection into a running session is future work. |
| Binary / non-UTF-8 file lands in `memory/shared/` | `compose_shared_context_block` skips the file silently rather than corrupting the whole prompt. |
| Server crash mid-upload | Temp file under `.tmp/` is swept on boot. |

## Testing

- `packages/cluster/tests/test_rooms_file_storage.py` — 13 tests covering save/read/delete/cleanup, size ceiling, crash-cleanup.
- `packages/cluster/tests/test_rooms_shared_files.py` — 16 tests covering upload/list/delete REST, fan-out, upsert, MIME/size rejection, membership add/remove hooks, filename sanitisation.
- `packages/machine/tests/test_daemon.py::TestSharedFileHandlers` — 7 tests for the write/delete/dispatch path.
- `packages/machine/tests/test_materialize.py::TestMemoryMaterialize::test_creates_empty_shared_dir`.
- `packages/agent/tests/test_memory_shared.py` — 8 tests for `compose_shared_context_block`.

## Backup / operations

`~/.anygarden/` is a single backup root: `tar` of the directory captures the
DB, room attachments, per-agent memory, and the machine token. There is no
separate restore procedure for `room_files/` — lost files surface on the
next fan-out (the frame read fails) and get logged; recovery is
"re-upload".

## Future work

- Machine-reconnect hook to re-sync the full shared file set when a daemon
  comes back online (`resync_machine` service function is wired but not
  invoked yet — needs a hook in the machine WS handler).
- Live-injection on file arrival: today, a running agent sees new files
  only on the next prompt composition.
- Binary / image attachments: pick a storage quota strategy and a way to
  present them to the engine that doesn't blow up the token budget.
- Central store + read-only symlinks once per-agent disk usage becomes a
  bottleneck (see the plan's decision 1 for the trade-off).

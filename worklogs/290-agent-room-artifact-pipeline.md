# feat(rooms): agent → room artifact pipeline (#290 Phase B)

- Commits (5, will squash-merge as one):
  - `66fe7df` add room_artifacts table for agent-produced files
  - `0636094` outbox watcher for agent-produced room artifacts
  - `f02d974` server ingestion + WS broadcast for room artifacts
  - `4902fce` list/download/delete HTTP endpoints for artifacts
  - `d3b0b17` artifacts dialog + agent outbox prompt
- Author: Changyong Um
- Date: 2026-04-28
- PR: #290 issue (PR number assigned on push)

## Situation

Issue #290 Phase A landed (#291) and shipped ANSI rendering for codex's
existing terminal-text replies, but the deeper gap remained: agents had
no way to surface real artifacts (screenshots, charts, big data dumps)
into a room. The existing `room_shared_files` channel (#246) is
deliberately one-way (user → agent, text-only, 256 KiB), with the
service comment pinning that direction: *"server is the source of
truth … machine never sync-backs their contents"*. Phase B inverts
that: agent drops a file under `memory/outbox/`, machine watches the
directory, server fans the bytes out to a new artifact panel in every
room the producing agent participates in.

## Task

Implement an agent → user pipeline end-to-end:

- Distinct DB table for the new flow (don't overload `room_shared_files`).
- Machine-side polling watcher mirroring the proven `_flush_memory_updates`
  pattern, including idempotent re-delivery on reconnect.
- New machine→server frame plus server-side validation (MIME whitelist,
  size cap, sha256 verification, fan-out to every placed room).
- HTTP read endpoints for the panel (list / download / delete) gated by
  the existing room-participant dependency.
- Frontend gallery dialog with image previews + WS event hookup so the
  panel refreshes without polling.
- Agents-side discoverability: extend the auto-built `AGENTS.md` so
  agents learn the outbox convention without manual prompting.

Constraints worth surfacing for future-me: WebSocket frame envelope is
1 MiB so raw cap is 768 KiB (after base64 inflation). Must not break
the existing memory/notes.md sync-back path that B2 piggy-backs on.

## Action

**B1 — DB & model** (`66fe7df`)
- `packages/cluster/doorae/db/migrations/versions/035_room_artifacts.py`:
  new `room_artifacts` table with `(room_id, sha256)` unique constraint
  and `ix_room_artifacts_room_id`. Producer FK is SET NULL on agent
  delete; room FK is CASCADE.
- `db/models.py:610` — `RoomArtifact` SQLAlchemy model alongside
  `RoomSharedFile`.
- `tests/test_room_artifacts_model.py` — schema DDL guard, round-trip
  insert/select, dedup constraint, SET NULL / CASCADE behaviour.
- `tests/test_migrations.py` — version pin bumped 034 → 035.

**B2 — Machine outbox watcher** (`0636094`)
- `packages/machine/doorae_machine/protocol/frames.py:262` —
  `RoomArtifactProducedFrame` with base64 content + sha256 + size.
- `daemon.py:139,675,720` — `_artifact_last_hash` cache,
  `_flush_outbox_artifacts` method called from
  `_report_actual_state`. Mirrors `_flush_memory_updates` patterns
  exactly: silent skip on missing accessor, OS errors, oversize files,
  disallowed MIME, subdirs/symlinks. `ARTIFACT_MAX_BYTES = 768 KiB`,
  `ARTIFACT_ALLOWED_MIMES` covers PNG/JPEG/GIF/WebP/SVG + the same
  text whitelist as #246.
- `spawner.py:382` — pre-create `memory/outbox/` (0700) symmetric with
  `memory/shared/`.
- `tests/test_daemon.py` — `TestOutboxArtifactSyncBack290` (7 cases).

**B3 — Server ingestion** (`f02d974`)
- `rooms/artifact_storage.py` — new module mirroring `file_storage.py`
  but for binary blobs (atomic temp-then-rename, sha256, orphan sweep,
  `read_bytes` instead of `read_text`).
- `rooms/artifacts.py` — `handle_artifact_produced(session, frame, *,
  artifact_files_dir)`: validate frame, look up rooms via
  `Participant.agent_id`, insert per-room rows, catch `IntegrityError`
  on (room_id, sha256) collision and roll back the failed flush. Plus
  `list_artifacts`, `get_artifact`, `delete_artifact` for the HTTP layer.
- `ws/protocol.py` — new outgoing `RoomArtifactAddedOut` /
  `RoomArtifactRemovedOut` frames added to the `OutgoingFrame` union.
- `ws/machine_handler.py:117` — `room_artifact_produced` branch invokes
  the service and broadcasts `room_artifact.added` per inserted row.
- `config.py:13,59` — `artifact_files_dir` defaults to
  `~/.doorae/artifact_files` (sibling of `room_files_dir`).
- `tests/test_room_artifacts_service.py` (11 cases) covering fan-out,
  dedup, MIME/size/sha256 rejection, unknown-agent guard, list/get/delete.

**B4 — HTTP endpoints** (`4902fce`)
- `rooms/router.py` — new `RoomArtifactOut` schema and three endpoints:
  `GET /rooms/{id}/artifacts` (list), `GET /rooms/{id}/artifacts/{aid}`
  (download with `Content-Disposition: inline`, disk read offloaded to
  `asyncio.to_thread`), `DELETE /rooms/{id}/artifacts/{aid}` (broadcasts
  `room_artifact.removed`). All gated by `_require_room_participant`.
- `tests/test_rooms_artifacts_endpoints.py` (8 cases) end-to-end through
  the FastAPI app.

**B5+B6 — Frontend + agent prompt** (`d3b0b17`)
- `frontend/src/lib/roomArtifacts.ts` — list/delete fetch helpers + a
  blob-URL fetcher (`fetchArtifactBlobUrl`) so `<img src=>` works
  without exposing GET endpoints to cookie auth.
- `frontend/src/components/RoomArtifactsDialog.tsx` — gallery dialog:
  image MIMEs preview inline via blob URL (auto-revoke on unmount),
  others render as file cards. Refreshes on open and on the two new
  WS-rebroadcast window events.
- `frontend/src/hooks/useWebSocket.ts:97` — re-broadcast
  `room_artifact.added` / `room_artifact.removed` as window events,
  same pattern as `task.updated`.
- `frontend/src/pages/ChatPage.tsx` — mount the dialog and add an
  산출물 button next to 공유 파일.
- `packages/machine/doorae_machine/spawner.py:268` — append a "Sharing
  artifacts with the user" section to the auto-generated `AGENTS.md`,
  matching the existing memory/notes.md guidance section's style.

## Decisions

Mining `.tmp/plan-290-agent-artifacts-and-ansi.md` §3.2:

- **D2 — Drop directory `memory/outbox/`**. Considered `workspace/share/`
  (matches cwd convention but ambiguous semantics) and a configurable
  env var (deferred — extra surface). Outbox sits next to `memory/shared/`
  + `memory/notes.md` so agents learn the convention by analogy.
- **D3 — MIME whitelist: text whitelist (#246 superset) + image PNG / JPEG /
  GIF / WebP / SVG**. PDF rejected for v1 (would force pdf.js into the
  bundle just for previewing). Other binaries deferred — pure download
  with no preview adds clutter.
- **D4 — WS frame transport, 768 KiB raw cap**. Considered: HTTP upstream
  channel for chunked uploads. Rejected for v1 because daemon→server
  HTTP would need its own auth model and chunking logic; a 768 KiB cap
  covers low-resolution screenshots which is the headline use case.
  When this caps real usage, the follow-up issue is HTTP upstream + size
  bump — not in scope here.
- **D5 — New `RoomArtifact` table, not extending `RoomSharedFile`**.
  Considered: nullable `produced_by_agent_id` on the existing table.
  Rejected because the two flows have inverted directions (agent → user
  vs user → agent) and policies (binary + larger cap vs text + 256 KiB);
  collapsing them forces every fan-out / system-prompt builder / authz
  check to disambiguate by `if file.produced_by_agent_id is not None`.
  Extra table now is cheaper than coupled drift later.
- **D6 — Single direction (agent → user only) for v1**. Other agents'
  `memory/shared/` does NOT auto-pull artifacts, even though the
  symmetry is tempting. Out of scope: deciding which artifacts to fan
  in (mention-driven? all?), reconciling the binary-vs-text MIME
  policies between the two flows, and adding system-prompt anchors so
  agents discover received artifacts. All deferred to a follow-up.
- **D7 — Polling, not inotify**. The existing
  `_flush_memory_updates` does the same and works in production.
  inotify adds platform variance (no Windows for free) and a parallel
  scheduler. Switch when "30s artifact lag" becomes user-visible
  feedback.
- **D8 — Fan-out to every placed room (still partially open)**. Plan
  flagged this as the open routing question. v1 ships fan-out + sha256
  dedup; if the pattern surfaces *"this artifact belonged to room A,
  not B"* complaints, follow-up adds room-tagged filenames or
  last-spoken-room scoping. Captured in the issue body as a known
  unresolved item.

Assumptions worth re-checking later:
- 768 KiB caps real-world artifact volume. Re-check if agents start
  truncating screenshots to fit.
- Polling interval (piggy-backed on `report_actual_state`, default 30s)
  is acceptable lag for "show me the screenshot you just took".
- Dialog (vs permanent right-rail panel) is sufficient UX. Original
  plan said sidebar; chose dialog for v1 because the chat layout has
  no existing right-rail and adding one is its own design decision.

## Result

End-to-end: an agent that writes any whitelisted file under
`memory/outbox/` causes the daemon to ship the bytes server-side, the
server persists per-room rows + disk blobs and broadcasts a WS event,
and the user sees the file appear in a room dialog with an inline
image preview or a download link.

Test coverage:
- Cluster: 839 → 858 passing (+ 19 new across artifact model, service,
  endpoints).
- Machine: 320 → 327 passing (+ 7 outbox watcher cases).
- Frontend: 357/357 unchanged (dialog has no unit test yet — relies on
  type-check + manual E2E).
- All packages individually green; existing
  `tests/test_integrations/test_openai.py` failure pre-dates this work
  (env var missing in the worktree, unrelated to the diff).

Pending:
- Manual E2E after deploy: have codex actually `cp screenshot.png
  memory/outbox/` and verify it surfaces in 테스트룸4.
- Frontend unit test for `RoomArtifactsDialog` (mock fetch + WS events).
- Sidebar UX upgrade (vs dialog) — separate follow-up issue when ready.
- D8 routing refinement based on real usage feedback.

# CHANGELOG


## Unreleased


## v0.3.0 (2026-04-16)

### Features — room-query UX (#55)

- Structured room-query UX with banner chips and result cards
  ([#55](https://github.com/e7217/doorae/issues/55),
  [#59](https://github.com/e7217/doorae/pull/59))
  — source-room banner transitions pending → completed/timeout
  by `query_id`; target-room forward bubble gets a source badge;
  original room renders a collapsible result card per agent
  response. Server stamps `room_query` / `room_query_forward` /
  `room_query_result` metadata; no new WS frame types.

### Features — presence (#54)

- Unify agent liveness via `PresenceService` + UI indicator
  ([#54](https://github.com/e7217/doorae/issues/54),
  [#60](https://github.com/e7217/doorae/pull/60))
  — single read-through service for "is this participant
  responsive right now?" backed by `ConnectionManager` (truth)
  with `Agent.last_heartbeat_at` fallback. `GET /rooms/{id}`
  exposes `online` + `last_seen_at`; WS broadcasts
  `presence_update` frames. `[ROOM_QUERY]` `expected_count` now
  excludes offline agents so stale participants don't force a
  timeout.

### Features — sidebar

- Drag-and-drop reorder for pinned rooms in sidebar
  ([#47](https://github.com/e7217/doorae/issues/47),
  [#51](https://github.com/e7217/doorae/pull/51))
- Hover `...` menu for rename + delete room
  ([#46](https://github.com/e7217/doorae/pull/46),
  [#48](https://github.com/e7217/doorae/pull/48))

### Features — rooms

- Delete-room UI + tighten authz + WS broadcast
  ([#45](https://github.com/e7217/doorae/pull/45))
  — owner/admin-only DELETE endpoint, cascade cleanup,
  `room_deleted` WS frame so other sessions drop the room
  without a refetch round-trip.

### Fixes — room routing

- Route direct-typed `#RoomName` mentions to the target room
  ([#53](https://github.com/e7217/doorae/issues/53),
  [#57](https://github.com/e7217/doorae/pull/57))
  — frontend now converts plain `#Name` text to the
  `<#room:id>` token before sending when the name matches
  exactly one known room, so typed mentions route the same as
  autocomplete-selected ones. Duplicate-name / unknown-name
  fallbacks preserved.
- Unify participant membership + `JoinRoomOut` broadcast
  ([#50](https://github.com/e7217/doorae/issues/50),
  [#52](https://github.com/e7217/doorae/pull/52))
  — the auto-join of a representative agent now emits a
  `JoinRoomOut` frame on every relevant WS session so the SDK
  subscribes to the new room in time for the upcoming broadcast
  (race that previously caused `(1/N)` miscounts in
  `[ROOM_QUERY]`).
- Break the `[ROOM_QUERY]` forwarding loop
  ([#42](https://github.com/e7217/doorae/pull/42))
  — the server no longer re-attaches `room_query` metadata to
  agent-originated forwards; combined with the SDK's
  `<#room:…>` strip, the ad-infinitum recipient-forwards-again
  loop is closed at the source.
- Unify REST `metadata` field + prevent duplicate
  `room_query_forward` ([#61](https://github.com/e7217/doorae/pull/61),
  [#62](https://github.com/e7217/doorae/pull/62))
  — REST `MessageOut` now returns `metadata` (was
  `extra_metadata`) so history-loaded messages render the
  forward / result cards identically to WS-arrived ones.
  Target-room forwards are now emitted by the target room's
  representative only, not every agent that saw the question.
- Add `min-h-0` to ChatArea wrapper to restore inner scroll
  ([#63](https://github.com/e7217/doorae/pull/63),
  [#64](https://github.com/e7217/doorae/pull/64))

### Features — admin

- Allow admins to remove room participants
  ([#40](https://github.com/e7217/doorae/pull/40))


## v0.2.0 (2026-04-15)

### Features — anonymous guest participation (RFC #22)

- Allow anonymous guest rows on users table
  ([#24](https://github.com/e7217/doorae/pull/24))
- Room invite links with admin-only lifecycle
  ([#25](https://github.com/e7217/doorae/pull/25))
- Guest identity + /auth/guest + forbid_guest gate
  ([#26](https://github.com/e7217/doorae/pull/26))
- Guest branch in the WebSocket send path
  ([#27](https://github.com/e7217/doorae/pull/27))
- Trim the guest read surface
  ([#28](https://github.com/e7217/doorae/pull/28))
- Guest lifecycle job + metrics + final docs
  ([#31](https://github.com/e7217/doorae/pull/31))

### Features — membership / UI

- Notify agent of dynamic room join via add_participant
  ([#17](https://github.com/e7217/doorae/pull/17))
- Notify user on add_participant via WS
  ([#19](https://github.com/e7217/doorae/pull/19))
- Show room participant list in a header popover
  ([#32](https://github.com/e7217/doorae/pull/32))
- Allow admins to remove room participants
  ([#40](https://github.com/e7217/doorae/pull/40))

### Fixes

- Machine deletion cascade and error surfacing
  ([#1](https://github.com/e7217/doorae/pull/1))
- Delete agent's DM room when the agent is deleted
  ([#12](https://github.com/e7217/doorae/pull/12))

### Docs

- WS frame tables in §1.5 synced with protocol.py
  ([#21](https://github.com/e7217/doorae/pull/21))
- Anonymous guest participation RFC (design §11)
  ([#23](https://github.com/e7217/doorae/pull/23))

## v0.1.0 (2026-04-14)

### Chores

- Switch license to Apache-2.0 and update author
  ([`a4f1d0a`](https://github.com/e7217/doorae-cluster/commit/a4f1d0a8ddd6b1641dd08ed63c42f60b66576635))

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>

### Continuous Integration

- Add python-semantic-release for automatic versioning
  ([`eb5269a`](https://github.com/e7217/doorae-cluster/commit/eb5269a8799737008802d19f9838470dddfce195))

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>

### Features

- Initial release — doorae-cluster v0.2.0
  ([`47bed32`](https://github.com/e7217/doorae-cluster/commit/47bed3254a656e208e1d765b6e8ece22707043f2))

Extracted from e7217/doorae monorepo (formerly doorae-server). Renamed package doorae-server →
  doorae-cluster.

Includes: - FastAPI chat server with WebSocket + REST API - SQLAlchemy async DB with Alembic
  migrations (11 versions) - Auth system (JWT, machine tokens, admin/owner roles) - Agent & machine
  management APIs - React/Vite frontend (SPA) - Prometheus observability - doorae-machine dependency
  via GitHub source

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>

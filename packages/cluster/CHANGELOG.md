# CHANGELOG


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

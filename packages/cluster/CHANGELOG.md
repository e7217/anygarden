# CHANGELOG


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

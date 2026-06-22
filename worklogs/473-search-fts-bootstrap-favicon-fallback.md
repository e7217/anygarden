# fix(search): create messages_fts on fresh-DB bootstrap; 404 for asset-like SPA paths (#473)

- Commit: `5a3e98e` (5a3e98e1429e0e603d3e057635f2e216be65ea0a)
- Author: Changyong Um
- Date: 2026-06-22T12:29:38+09:00
- PR: #473

## Situation

On a fresh install, authenticated message search returned a 500. The `messages_fts` FTS5 virtual table and its three sync triggers were created **only** by Alembic migration 008. But the fresh-DB bootstrap path — `_ensure_schema_ready` Case 2 (`app.py`) — materialises the schema via `Base.metadata.create_all` + an inline `alembic_version` stamp and never replays migrations. `create_all` builds the ORM-declared tables but not the raw FTS5 DDL, so `messages_fts` was permanently absent on any DB bootstrapped this way (`make server` / `server-dev` / `anygarden server` + fresh DB). Every `GET /api/v1/search` then hit `OperationalError: no such table: messages_fts`, which had no handler and surfaced as an unhandled 500.

Separately, the SPA catch-all (`spa_fallback`) returned `index.html` (200, `text/html`) for **every** unmatched path. `/favicon.ico` therefore served the HTML shell as an icon, and browsers tried to parse HTML as a favicon.

## Task

- Make a fresh-bootstrapped DB have a working `messages_fts` index (table + triggers) so search works on a brand-new install, without touching the frozen migration 008.
- Ensure a genuinely-missing FTS index degrades to a 503 rather than leaking a 500.
- Make asset-like requests (e.g. `favicon.ico`) that don't resolve to a real file return 404, while keeping extensionless SPA routes (`/rooms/abc`) returning `index.html`.

## Action

- **New `packages/cluster/anygarden/db/fts.py`** — extracted the FTS5 table + 3 trigger DDL (copied verbatim from migration 008) into a `MESSAGE_FTS_STATEMENTS` tuple and `async def create_message_fts(conn)`. All statements use `IF NOT EXISTS`, so the helper is idempotent. This becomes the living source of truth; migration 008 is deliberately left frozen (a historical migration must not import live app code).
- **Bootstrap wiring** — `app.py` `_ensure_schema_ready` Case 2: right after `create_all`, inside the same `engine.begin()` transaction, `if engine.dialect.name == "sqlite": await create_message_fts(conn)`. Joining the existing transaction preserves the create_all+stamp atomicity/retry-safety guarantee. FTS5 is SQLite-only, hence the dialect guard.
- **Search defensive 503** — `api/v1/search.py`: refactored both query branches to build `sql` + `params`, then wrapped the single `db.execute` in `try/except OperationalError → HTTPException(503, "Search index unavailable")`. A missing index (legacy DB, or a Postgres backend without FTS5) degrades instead of 500ing.
- **SPA fallback 404** — `app.py`: added pure helper `_is_asset_like_path(path)` (`"." in path.rsplit("/", 1)[-1]`) and used it in `spa_fallback` so an unmatched asset-like path returns `Response(status_code=404)` while extensionless routes still serve `index.html`.
- **conftest** — `tests/conftest.py` `engine` fixture: after `create_all`, also call `create_message_fts` for sqlite so search-endpoint integration tests run against a real index (purely additive — does not affect non-search tests).
- **Tests** — `tests/test_search_fts_bootstrap.py`: a fresh-DB test (builds its own sqlite file via `_ensure_schema_ready`, not conftest) asserting `messages_fts` exists and that an inserted message becomes findable via FTS `MATCH` (proving triggers landed); plus an endpoint test (app built with `create_all` only, no FTS) asserting `GET /api/v1/search` returns 503, not 500. `tests/test_spa_fallback_favicon.py`: unit tests for `_is_asset_like_path` (extensioned → asset, extensionless → SPA, dot-only-in-non-final-segment → SPA) and a `TestClient` integration test (temp static dir with index.html, no favicon) asserting `/favicon.ico` → 404 and `/rooms/<id>` → index.html.

## Decisions

- **FTS helper extraction (chosen) over `alembic upgrade head` in Case 2 or 008 importing app code.** Replacing `create_all` with a base→head migration replay would change the atomic create_all+stamp semantics (intentional retry-safety) and boot performance — too broad a blast radius. Having frozen 008 import a live helper risks breaking historical replay if the helper later changes. Extracting `db/fts.py` as the living source of truth lets bootstrap, tests, and any future path reuse one entry point while 008 stays an untouched snapshot. DDL is duplicated between 008 and `db/fts.py` by design.
- **Extension heuristic over an explicit `/favicon.ico` route.** A dedicated handler catches only favicon; the basename-extension heuristic generally separates SPA routes (no extension → index.html) from asset requests (`*.ico`, `*.txt` → 404). `/assets/*` is served by the StaticFiles mount before the catch-all, so it is unaffected. Current frontend routes carry no extension, so no real SPA route is shadowed.
- **RED before GREEN.** Both fixes were driven test-first: the search test reproduced the original `OperationalError`-as-500 (RED) before the bootstrap fix; the favicon helper test failed with `ImportError` (RED) before the helper existed.

## Result

Fresh-DB bootstrap now creates `messages_fts`, so authenticated search works on a new install; a missing index degrades to 503 instead of 500; and `/favicon.ico` (and other unmatched asset-like paths) return 404 while SPA routes still serve `index.html`. Full cluster suite: **1190 passed, 1 deselected** (the `slow` marker), 1 pre-existing unrelated deprecation warning; no flaky/teardown failures this run. Lint clean on all changed files. The conftest FTS addition is additive and broke no previously-passing test.

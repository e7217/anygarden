# test: build each router's routes once per worker instead of per test app (#1001)

- Commit: `14e9443` (14e9443f3928fabe205e0f8442b3e60968389a80)
- Author: Changyong Um
- Date: 2026-11-10T09:28:34+09:00
- PR: #1001

## Situation

`create_app` is called for approximately 1,300 tests per suite run. Each call invoked FastAPI's `include_router`, which re-analyses every endpoint's dependencies and regenerates pydantic response schemas — costing roughly 0.19 s per app and accounting for a substantial share of total suite runtime. Since routes were rebuilt identically for every app, that work was largely redundant.

## Task

- Cache the route objects built by the first `include_router` call for each router.
- For subsequent apps using the same router (with no extra options), copy cached routes and rebind each copy's request handler to the new app.
- Merge startup hooks and lifespans exactly as `include_router` does.
- Leave all product code untouched; the optimisation lives entirely in the test conftest.
- Verify that `dependency_overrides` on one app do not leak to another, and that all apps receive identical route sets.

## Action

**`packages/cluster/tests/conftest.py`** — added `_include_router_once` function (lines ~52–109):
- Module-level dict `_included_routes` maps `id(router)` → `(router, len(router.routes), cached_routes)`.
- Monkeypatches `FastAPI.include_router` with `_include_router_once`.
- On cache miss (or stale entry detected via `len(router.routes)` check), calls the original `_fastapi_include_router` and stores the new routes.
- On cache hit, shallow-copies each `APIRoute` / `APIWebSocketRoute`, rebinds `dependency_overrides_provider = app`, regenerates `route.app` via `request_response(route.get_route_handler())` (or `websocket_session(get_websocket_app(...))` for WS routes), and appends to `app.router.routes`.
- Falls through to the original path for any `include_router` call that passes keyword `options` (prefix, tags, etc.).

**`packages/cluster/tests/test_conftest_route_cache.py`** — new file (43 lines):
- `test_dependency_overrides_stay_on_their_own_app`: creates two apps, overrides `get_admin_identity` on each independently, asserts that overriding one does not affect the other's response status.
- `test_each_app_gets_the_same_routes`: asserts that two freshly created apps have the same route type/path list and identical OpenAPI path sets.

## Decisions

- **Copy-and-rebind rather than share route objects**: sharing a single `APIRoute` across apps would cause `dependency_overrides_provider` mutations on one app to bleed into others. A shallow copy is the minimum required to give each app its own handler closure.
- **Cache invalidation via `len(router.routes)`**: the number of routes on a router is a cheap proxy for "the router has changed"; a more precise content hash would be costly and unnecessary for the test-only use case.
- **Monkeypatching `FastAPI.include_router` at module load** (rather than a fixture): ensures the patch is in place for every `create_app` call without requiring test authors to opt in. The patch scope is intentionally confined to the test package.
- **Fallback for `options`-bearing calls**: includes that add a prefix, tags, or other overrides cannot safely share cached routes (the route paths and metadata differ), so they always go through the original path. This keeps the optimisation transparent.
- **Product code unchanged**: the entire change lives in `conftest.py` and the new test file. No application logic is affected.

## Result

- `create_app` time reduced from ~0.19 s to ~0.006 s per call.
- Full cluster suite on 4 workers locally: 267 s → 228 s (−15 %).
- Two new tests confirm isolation of `dependency_overrides` across apps and route-set equivalence.
- The override test is specifically designed to fail if a cached route retains the first app's handler rather than its own.

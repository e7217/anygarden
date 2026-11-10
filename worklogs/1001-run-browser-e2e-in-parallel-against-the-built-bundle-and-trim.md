# ci: run browser E2E in parallel against the built bundle and trim slow tests (#1001)

- Commit: `175e9e5` (175e9e547120645a7dc3499281b86cff8d443d77)
- Author: Changyong Um
- Date: 2026-11-10T09:15:54+09:00
- PR: #1001

## Situation

Build frontend was the slowest CI job, taking ~12.7 minutes end-to-end. The bottleneck was Playwright's 91 E2E tests running one at a time (workers: 1 on CI), consuming ~9.4 minutes of that budget. The tests stub REST calls and share no process state, so there was no technical reason they had to be serialised. Additionally, the build step was placed after the E2E run, meaning a failed build only surfaced late.

## Task

- Reduce E2E wall-clock time on CI by running tests in parallel.
- Ensure the production bundle (not the dev server) is what Playwright tests against on CI, since parallel workers loading the dev server pushed first-paint times past the 5 s `expect` timeout.
- Fix a flaky assertion in `right-rail.spec.ts` that read the drawer width mid-transition.
- Remove redundant migration up/down tests whose coverage is already provided by `test_downgrade_to_base_and_back`.
- Make `test_spawner`'s hanging-wait kill test not wait out the real 10 s `PROC_WAIT_TIMEOUT`.

## Action

- **`.github/workflows/ci.yml`**: Moved the `Build (typecheck + bundle)` step to run *before* `Install Playwright Chromium` / E2E, so the bundle is ready when Playwright starts. E2E now runs against the pre-built bundle via `vite preview`.
- **`packages/cluster/frontend/playwright.config.ts`**: Changed `workers` from `1` to `4` on CI. Added conditional `webServer.command`: on `CI`, runs `npm run preview` (serves the built bundle); locally keeps `npm run dev` (edit-and-rerun loop, no build step). Added explanatory comments for both changes.
- **`packages/cluster/frontend/e2e/right-rail.spec.ts`** (line 351): Replaced `expect(await width(sidebar(page))).toBe(256)` with `await expect.poll(() => width(sidebar(page))).toBe(256)` to tolerate the CSS slide-in transition reporting 255.99… before settling.
- **`packages/cluster/tests/test_migrations.py`**: Dropped tests for migrations 048, 049, 060, 061, 062 (up/down). These only asserted schema shape already verified by `test_downgrade_to_base_and_back`. Kept tests for 047, 063, and 082 because they assert data integrity beyond schema shape (302 lines removed).
- **`packages/cluster/tests/machine/test_spawner.py`** (line 1109): Added `patch("anygarden.machine.spawner.PROC_WAIT_TIMEOUT", 0.1)` alongside the existing `terminate_tree` patch so the hanging-wait kill test completes in ~0.1 s instead of waiting the real 10 s.

## Decisions

- **4 workers vs. more**: 4 was chosen as a conservative parallelism level that fits standard GitHub-hosted runners (2 vCPU, 7 GB RAM). More workers risk OOM or port conflicts; fewer would leave performance on the table.
- **`vite preview` vs. keeping the dev server on CI**: The dev server compiles routes on first request; with 4 concurrent workers that caused first-paint latency to exceed the existing 5 s `expect` timeout. Serving the pre-built bundle via `vite preview` avoids per-request compilation entirely. The alternative of raising the timeout was rejected because it would mask real slowness.
- **Moving the build step earlier**: Build was originally placed at the end of `build-frontend` job, after E2E. Moving it before E2E was the simplest way to have the bundle ready for `vite preview` without restructuring the job or adding a separate build job. The tradeoff is that a build failure now aborts E2E; this is acceptable because a broken build makes E2E results meaningless.
- **Dropping 048/049/060/061/062 migration tests**: These tests only recreated the schema and checked column existence — shape-only coverage already provided by the broader downgrade-and-back round-trip. Removing them reduces test time and maintenance overhead. Tests for 047/063/082 were kept because they seed and assert row-level data, which the round-trip test does not.
- **`expect.poll` over a fixed delay**: Polling lets the assertion resolve as soon as the transition completes rather than waiting a predetermined sleep, keeping the test fast in the common case while tolerating slow CI runners.

## Result

- Playwright suite wall-clock time drops from ~9.4 min (1 worker) to ~44 s (4 workers, `CI=1` locally reproduces this).
- `right-rail.spec.ts` drawer-width assertion is no longer flaky on transitions.
- `test_migrations.py` is 302 lines shorter; the removed tests added no unique coverage.
- `test_spawner`'s hanging-wait kill test no longer blocks for 10 s.
- Local developer workflow is unchanged: `npm run dev` is still used when `CI` is unset.

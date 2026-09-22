# chore(frontend): clean-checkout housekeeping — dev-token noise, e2e setup, admin code-splitting, audit triage (#651)

- Commits: `6870571`, `5161144`, `6629e31`, `53ae0e9`
- Author: Changyong Um
- Date: 2026-09-22
- Issue: #651

## Situation

Bringing the service up from a clean checkout surfaced four unrelated rough edges. All four reproduced exactly as reported, but measuring each one moved two of them a long way from how the issue framed them.

1. **`npm run test:e2e` fails on a fresh clone.** No setup step installs the Playwright browser binary — not `make setup` (uv workspace only), not `packages/cluster/Makefile`'s `frontend` target (`npm install` only), and neither CONTRIBUTING.md nor README.md mentions Playwright. The issue read this as an abandoned suite. It is not: `.github/workflows/ci.yml:126` and `release.yml:40` both run `npx playwright install --with-deps chromium`. CI has always been correct. The gap is that the one command a contributor needs existed only inside a workflow file, where nobody debugging a local e2e failure would look.

2. **A single 1,077.75 kB entry chunk**, reproducible down to the file hash, with vite's >500 kB warning attached. `TopologyPage` was the only lazy route (`App.tsx:25`); the six admin pages and four LLM-gateway sections were static imports. Every one of those ten sits behind `AdminRoute`'s `user.is_admin` check, so a non-admin downloads code they can never render, before the login form paints.

3. **`npm audit`: 20 vulnerabilities (1 low, 10 moderate, 8 high, 1 critical).** Tracing all 20 dependency paths inverted the headline. 19 never reach a deployed asset, and the lone critical is the test runner. Exactly one package is both shipped to browsers and fixable without a major bump.

4. **A 404 on `/api/v1/auth/dev-token` logged on every unauthenticated page load.** The behaviour is correct — `auth/routes.py:146` 404s unless `config.dev`. But `config.dev` needs `ANYGARDEN_DEV=1` (`config.py:48`) and no script in the repo sets it (`make dev` sets only `ANYGARDEN_PORT`), so in practice the request can only ever produce console noise.

## Task

Fix each item at the size the evidence supports rather than the size the issue implies, keep the four changes in separate commits so any one can be reverted alone, and write the dependency triage down so the next person does not repeat it.

## Action

- **`dev-token` guard (`6870571`)** — wrapped the auto-login branch in `import.meta.env.DEV`. Vite folds the constant at build time, so the call leaves the production bundle entirely instead of being skipped at runtime. The only combination given up is "built static assets served by an `ANYGARDEN_DEV=1` server", which is undocumented and unreachable from any dev workflow, since `make dev` serves through the vite dev server where `DEV` is true.

- **Playwright setup path (`5161144`)** — added `make -C packages/cluster e2e-setup` and pointed CONTRIBUTING.md's "Checks before you push" at it. Two things only surfaced by running it:

  Copying CI's command verbatim produced a target that *cannot run*. `--with-deps` shells out to `sudo apt-get` and dies with "sudo: a terminal is required to read the password". A discoverable command that always fails is not an improvement, so the target installs the browser only and CONTRIBUTING.md documents `playwright install-deps chromium` as the separate sudo step.

  `make help` then did not list the new target — or the existing `e2e` one. Its grep was `^[a-zA-Z_-]+:` and neither name survives a character class without digits. Widened to `[a-zA-Z0-9_-]`. A setup target invisible to `make help` would not have fixed the reported problem.

  Rejected a `postinstall` hook: `make dev` runs `npm install --silent` on every start, so it would put a browser-download check in the daily loop to serve the few contributors who run e2e.

- **Admin code-splitting (`6629e31`)** — the six admin pages and four gateway sections moved to `lazy()`. The sections are named exports, so they remap onto `default`. Two placement decisions:

  The page-level `Suspense` sits *inside* `AdminRoute`, after the auth checks, so a non-admin is redirected without requesting an admin chunk.

  The gateway sections get a boundary per route element rather than sharing `AdminRoute`'s. A section element renders at `AdminLLMGatewayPage`'s `<Outlet/>`, so a boundary there blanks only the content column, while the shared one would tear down the secondary sidebar and Apply footer on every tab switch. A pathless layout route would have been tidier but breaks the sections: they read `useOutletContext`, and nesting a second bare `<Outlet/>` overwrites that context with `undefined`.

  Also checked what *not* to split. `react-markdown`/`micromark`/`hast` stay eager — they render ChatPage's messages, the first screen after login, so deferring them moves first-load cost rather than removing it. `manualChunks` was rejected for changing cache behaviour but not total first-load bytes. `chunkSizeWarningLimit` was rejected for hiding the symptom. `FederationPreviewPage` needed nothing: `grep` on the built bundle shows rollup already tree-shakes the DEV-only preview away.

- **`react-router-dom` 7.14.1 → 7.18.4 (`53ae0e9`)** — semver minor, clearing 8 advisories against `react-router <=7.18.1` including GHSA-49rj-9fvp-4h2h (RCE via vendored turbo-stream) and GHSA-chx6-hx7r-mcp5 (DoS via inefficient route matching). The triage table lives in the commit message: group A is 10 build/test packages, group B is 6 that sit under `@lobehub/icons` → `@lobehub/ui` but tree-shake out of the bundle (`grep -c mermaid` on `index-*.js` is 0, because `EngineGlyph.tsx` imports subpaths), group C is the one shipped package fixed here.

  `npm audit fix --force` was not used: its fix for the lone critical is `vitest@5.0.1`, flagged `isSemVerMajor` — a three-major jump of the test runner. That advisory only applies while the Vitest UI server is listening, which `vitest run` never starts.

  A lockfile discovery came out of this. The repo tracks two and only the root one is live: npm resolves `packages/cluster/frontend` as a workspace member of `anygarden-monorepo`, so installs there read and write the root `package-lock.json`. `packages/cluster/frontend/package-lock.json` is vestigial and had already drifted — it still pinned `react-router 7.14.0` while the installed tree was 7.14.1. Confirmed `npm ci` in that directory resolves 7.18.4 after this change, so the bump does reach CI. Removing the stale file is left to its own change.

## Result

Bundle, from `vite build`:

| | before | after | delta |
|---|---|---|---|
| `index-*.js` | 1,077.75 kB | 933.86 kB | −143.89 kB (−13.4%) |
| gzip | 295.83 kB | 263.80 kB | −32.03 kB (−10.8%) |

Ten new on-demand chunks, largest `AdminFederationPage` at 33.11 kB. Still above vite's 500 kB warning — the remainder is the markdown pipeline ChatPage needs on first paint, so the warning stays and a further split is follow-up work, as planned.

`npm audit`: 20 → 18 (high 8 → 6). `dev-token` occurrences in the built `index-*.js`: 1 → 0.

Verification:

- 528 unit tests across 56 files, green.
- 13 Playwright e2e tests, green — run through the new `e2e-setup` target.
- `make -C packages/cluster -n` dry-run on all eight targets: none broken. `make help` now lists `e2e` and `e2e-setup`.
- Because lazy routes fail only at runtime, the admin surfaces were checked against the **production build** (`vite preview` over `../anygarden/static`, REST stubbed) with a scripted browser pass: all six admin pages and all four gateway sections render their heading, the gateway shell survives section switching, the `/admin/llm-gateway` index redirect and the `/admin/agents` → `/admin/machines` alias both work, neither guest param route bounces through `/login`, an unauthenticated visitor is still redirected to `/login`, and there are zero chunk-load errors. The network log confirms only `index-*.js` is fetched until an admin route is actually visited — the code-splitting claim, measured rather than inferred.
- The same routes were also walked in a dev server against a live node, which surfaced only pre-existing backend 503s (`llm-gateway/status`, `node/peers`) unrelated to this change.

No Python file is touched by this branch, so the Python suites were not run.

Out of scope, and candidates for follow-up: the `vitest` 2 → 5 major upgrade; replacing `@lobehub/icons` (one dependency accounts for 6 of the remaining 18 advisories, to render four brand icons); a further entry-chunk split below the 500 kB warning; and deleting the vestigial `packages/cluster/frontend/package-lock.json`.

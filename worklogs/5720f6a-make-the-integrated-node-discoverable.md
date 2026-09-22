# feat(cluster)+docs: make the integrated node discoverable (#648, #649)

- Commits: `9caa622`, `5720f6a`
- Author: Changyong Um
- Date: 2026-09-22
- Issue: #648, #649
- Related: #396 (unified entry point), ADR-007 `docs/decisions/007-federated-node-contract.md`

## Situation

`anygarden start` — one process serving the API and running agents — existed
only in `docs/runbook/local-node.md` and ADR-007. `grep "anygarden start"`
over `README.md` and `CONTRIBUTING.md` returned nothing, so the documented way
in was still `server` + `machine register` + `machine run`. Nothing there was
false; ADR-007 keeps all of it supported. But a reader concludes the machine
daemon is *required* on a single host and runs two processes plus a
registration step where one would do. The issue reporter reached exactly that
conclusion and had to be corrected by the maintainer (#648).

The same gap has a runtime face. `LocalExecutionBackend` is gated on
`local_node_data_dir`, and that setting has one writer — `start_node` in
`cli.py`. `make dev` runs uvicorn against the app factory directly, so the
value is `None`, the whole branch is skipped, and neither the logs nor the UI
mention it. A contributor finds out by adding an agent and watching nothing
happen (#649).

Two smaller faults surfaced while fixing these:

- `CONTRIBUTING.md` linked to `README.md#prerequisites` and
  `README.md#packages`. **Neither section existed.** Both anchors silently
  resolved to the top of the README.
- The `--reload` gap the issue raises as an open question turned out to be
  narrower than stated. Vite already proxies `/api` and `/ws` to `DEV_PORT`
  (`vite.config.ts:7,19-22`), so an integrated node on that port plus
  `npm run dev` gives real agent execution *and* frontend hot reload. What is
  actually missing is backend reload alone.

## Task

- Put the single-host path where a new reader will find it, without deleting
  the multi-host path that ADR-007 still supports.
- Say out loud, once, when a boot cannot run agents.
- Fix the two broken anchors while in the same files.

## Action

- **The disabled-execution notice (`9caa622`)** — added the missing `else` in
  `lifespan`. Two decisions:

  *Placement.* `configure_logging` runs inside `_startup_server`, so anything
  logged at the gate itself (`app.py:307`) would bypass the configured
  structlog pipeline. The line goes after `_startup_server`, in the branch
  that already distinguishes owner from no-owner.

  *Level.* `info`, not `warning`. For a multi-host deployment an API process
  without local execution is the normal and intended shape; warning there
  would be a false alarm on every production boot.

  Tests use `structlog.testing.capture_logs` and assert both directions —
  emitted exactly once under a server-only config, absent under
  `node_config`. `configure_logging` is stubbed in the test so it does not
  replace the capturing processor chain mid-startup.

- **README restructure (`5720f6a`)** — Quick Start splits into "Try it (single
  host)" and "Add more machines". `anygarden start` leads; `machine` is framed
  by the problem it solves (capacity, another OS, engines installed
  elsewhere). Content was moved rather than removed.

  New `## Prerequisites` and `## Packages` sections, which also repairs the
  two dead anchors. Versions are quoted from evidence, not guessed:
  `requires-python` per package (`anygarden` and `anygarden-machine` are
  `>=3.11`, the workspace and `anygarden-agent` are `>=3.12`), agent-ts
  `engines.node >=20`, and the CI workflow's pins.

  **No uv floor is claimed.** The first draft said "0.12+"; nothing in the
  repo pins a uv version, and the workspace installs and tests clean on
  0.7.13 — so the claim was not merely unsourced but false. Replaced with
  "any current release".

- **"Choosing a dev mode" in CONTRIBUTING (`5720f6a`)** — a two-row table
  mapping intent to command: `make dev` for server and frontend code,
  integrated node + `npm run dev` when agents must actually run, with what
  each gives up. The `--reload` restriction is stated with its consequence
  (stop and restart to pick up a backend change) instead of being discovered.

  The `--reload` gap itself is left open. Supporting it in integrated mode
  means deciding how uvicorn's reloader interacts with `NodeOwner.acquire()`
  and the lifetime of spawned agent processes — a design question, not a
  documentation one.

- **Not done: an admin-UI banner.** The issue offers it as optional. An empty
  machine list is the *correct* state for a multi-host deployment whose
  daemons have not attached yet, so a banner there would be a false positive
  for the audience most likely to see it — and surfacing the server's run mode
  to the frontend needs a new API field. The log line reaches the audience
  that actually has the problem: contributors, who are already watching a
  terminal.

## Result

- **cluster**: `tests/test_local_node.py` 25 passed, including the two new
  cases. Full suite: 1777 passed, 1 deselected, 0 failed (1152s).
- **Lint**: `ruff check --select F,E9` clean on both touched Python files.
  (`tests/test_local_node.py:823` has a pre-existing unused `import time`,
  present on `main` and left alone.)
- **Anchors**: all four cross-document links now resolve to real sections —
  `README#prerequisites`, `README#packages`,
  `README#develop-from-a-checkout`, `CONTRIBUTING#choosing-a-dev-mode`.
- **Claims checked against code**, not assumed: default port 8000
  (`config.py:43`), `anygarden stop` reporting only after cleanup
  (`cli.py:470`, "local process cleanup confirmed"), and the PyPI packages
  shipping a prebuilt web UI (`pyproject.toml` `artifacts` force-include).

Behavioural impact:
- A `make dev` boot now says `startup.local_execution_disabled` with the
  reason and the way out. Silence means local execution is on.
- The documented default for a single host is one process instead of three
  steps across two.
- Multi-host setup is unchanged, and now reads as the deliberate choice it is.

Deliberately out of scope: `--reload` support in integrated mode, the admin-UI
surface, and automated link checking in CI.

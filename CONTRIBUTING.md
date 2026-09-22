# Contributing to Anygarden

Thanks for your interest in improving Anygarden! This guide covers the essentials.

## Development setup

See [Prerequisites](README.md#prerequisites) for what you need installed, then:

```bash
make setup   # install the uv workspace + enable git hooks (run this, not a bare uv sync)
make dev     # run the cluster dev server + frontend
```

`make setup` also wires `core.hooksPath=.githooks` so `git pull` keeps the workspace
in sync — see the note in the README's [Develop](README.md#develop-from-a-checkout)
section for why a bare `uv sync` is not enough.

## Project layout

Anygarden is a `uv` workspace of four packages — see [Packages](README.md#packages):
`cluster` (server + web UI), `machine` (per-host daemon), `agent` (Python runtime),
and `agent-ts` (TypeScript runtime).

## Workflow

- Branch off `main` and open a pull request against it. PRs are **squash-merged**.
- Commit message convention: `{type}({scope}): {description} (#{issue})` — for example
  `fix(rooms): handle empty mention (#123)` or `feat(agents): add handoff tool (#456)`.
- Reference an issue where one exists.
- Change history lives as STAR-format worklogs under [`worklogs/`](worklogs) (one per
  change); browse existing entries for the format.

## Checks before you push

Run the same checks CI runs:

```bash
make test    # pytest across all Python packages
make lint    # ruff across all packages
```

For frontend changes, also type-check and bundle:

```bash
cd packages/cluster/frontend && npm run build
npm test                                       # vitest unit suite
```

The Playwright end-to-end suite needs a browser binary that `npm install` does
not fetch. Install it once, then run the suite against a dev server:

```bash
make -C packages/cluster e2e-setup             # one-off: downloads chromium
cd packages/cluster/frontend && npm run test:e2e
```

If chromium fails to launch on a bare Linux box it is missing shared libraries;
install them with `npx playwright install-deps chromium` (needs sudo). CI does
both steps at once as root via `npx playwright install --with-deps chromium` —
`e2e-setup` leaves the sudo half out so it works on a normal workstation.

## Database migrations

`make dev` does **not** run Alembic. The server migrates its own database during
startup, against the URL it actually opens, so normal development needs no
migration step.

When you write or test a migration by hand, name the target database explicitly —
Alembic has no default and will refuse to run without one:

```bash
cd packages/cluster
uv run alembic -x db_url=sqlite+aiosqlite:////tmp/scratch.db upgrade head
uv run alembic -x db_url=sqlite+aiosqlite:////tmp/scratch.db downgrade -1
uv run alembic -x db_url=sqlite+aiosqlite:////tmp/scratch.db upgrade head
```

Use a scratch file like the one above rather than your real database: with no
implicit default, `downgrade -1` can only damage a database you named yourself.
`ANYGARDEN_DB_URL` works too, and is what you want when the point is to migrate
the database the app will open.

## UI changes

Any work under `packages/cluster/frontend/` must follow the design system documented
in [`DESIGN.md`](DESIGN.md). Read the relevant section (color, typography, component
styling, spacing) before adding or restyling components, and check how existing
components apply it.

## License

By contributing, you agree that your contributions are licensed under the project's
[Apache-2.0 License](LICENSE).

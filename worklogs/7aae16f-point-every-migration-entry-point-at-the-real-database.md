# fix(dev): point every migration entry point at the real database (#647)

- Commits: `0a99e4d`, `6950028`, `a37e9df`, `08fff87`, `7aae16f`
- Author: Changyong Um
- Date: 2026-09-22
- Issue: #647
- Supersedes: `docs/e2e/2026-07-31/findings.md` 결함 1 (same defect, reported and left unfixed)

## Situation

`make dev` depended on a `migrate` target that upgraded a database the
application never opens. `alembic.ini` pinned
`sqlite+aiosqlite:///%(here)s/anygarden.db` — `packages/cluster/anygarden.db` —
while the app reads `ANYGARDEN_DB_URL`, defaulting to
`~/.anygarden/anygarden.db`. The target did not fail: it *created* the phantom
file, ran `001 -> head` against it, and printed a full success log. The file is
covered by `*.db` in `.gitignore`, so it never showed up in `git status`
either.

The residue was still on disk and made the age of the divergence visible:

| file | revision | messages |
|---|---|---|
| `packages/cluster/anygarden.db` | `068_agent_quota_availability` | 0 |
| `packages/cluster/doorae.db` | `040` | 0 |
| `~/.anygarden/anygarden.db` (real) | `072_drop_agent_collaboration_mode` | 515 |

A `doorae.db` at revision 040 means the mismatch predates the rebrand, which
matches the issue's finding that #174 never fixed the regression it was
written for.

This had already been reported. `docs/e2e/2026-07-31/findings.md` records the
identical defect with the identical evidence — and the live database three
revisions behind at the time. Its recommendation was to demote the ini URL to
a fallback. That was not applied, and a fallback would not have been enough
(see Action).

Verifying the issue turned up three more faults in the same blast radius:

1. `anygarden server migrate` — the operator's only manual recovery path when
   the integrated node refuses to boot on a schema mismatch (`app.py:509`) —
   **could not run outside the source tree**. `cli.py` set
   `script_location` to the relative `"anygarden/db/migrations"`, so an
   installed `anygarden` died with
   `CommandError: Path doesn't exist: anygarden/db/migrations`. Its two
   neighbours in `app.py`, `_alembic_action` and `_discover_head_revision`,
   both resolve it from `Path(__file__)`; only the CLI had drifted.
2. The same command ignored `ANYGARDEN_DB_URL` and `--config`, re-deriving
   `~/.anygarden/anygarden.db` by hand instead of going through `Settings`.
3. The Case 3 boot refusal told the operator to `cd anygarden-server` — a
   directory that does not exist — and to run alembic against the very
   `alembic.ini` that points at the phantom file. Following the recovery
   instructions would have stamped the wrong database.

## Task

- Stop `make dev` migrating the wrong file.
- Make manual migration work from an installed package, against the database
  the server would actually open.
- Make the class of defect structurally impossible rather than rarer, since
  "rarer" is what the July report effectively asked for and it survived.

## Action

- **`resolve_db_url` (`0a99e4d`)** — new `anygarden/db/migration_url.py`, split
  out of `env.py` so precedence is unit-testable (`env.py` runs as an Alembic
  script, not an importable module). Order: `-x db_url=` → a URL set on a
  programmatically built `Config` → `ANYGARDEN_DB_URL` → `alembic.ini`.

  That second rung is load-bearing and was not obvious: `app.py` startup and
  `tests/test_migrations.py` both construct `Config()` in code and set the URL
  on it. If the environment outranked them, a developer with
  `ANYGARDEN_DB_URL` exported would have their tests' throwaway databases
  redirected at their live one. `config_file_name is None` distinguishes the
  programmatic case from an ini-file read.

- **No default at all (`6950028`)** — `sqlalchemy.url` removed from
  `alembic.ini`, leaving a comment that explains why and how to re-pin it for
  a fixed deployment. Any default eventually disagrees with the app's
  configuration and the disagreement hides behind a success log; that is
  precisely what happened here. With all sources empty Alembic now fails,
  naming all three ways to specify a target.

  This also closes a hazard nobody had written down: `alembic downgrade -1`,
  the habitual round-trip check when authoring a migration
  (`worklogs/302-goals-backend-mvp.md`, `worklogs/309-agent-permission-level.md`),
  had an implicit target. The phantom database was accidentally shielding the
  live one. Removing the default removes the shield *and* the need for it.

- **Entry points (`a37e9df`)** — `migrate` target and the `dev:` dependency
  dropped from `packages/cluster/Makefile`, and the matching line from
  `scripts/dev.ps1`. Both places keep a comment saying the server migrates
  itself at startup, so the next reader does not "restore" the step.
  CONTRIBUTING gains the same note plus the scratch-database recipe for
  running alembic by hand.

- **`anygarden server migrate` (`08fff87`)** — `script_location` resolved from
  `Path(__file__)` like its neighbours, and the URL taken from
  `_load_server_settings` so `--db`, `--config` and `ANYGARDEN_DB_URL` all
  work. The success line now names the target: the original bug was invisible
  precisely because `Migrations applied.` never said applied *to what*.

- **Docs and the boot refusal (`7aae16f`)** — Case 3's recovery steps now name
  the failing database inline (`alembic -x db_url=<url> stamp <rev>`) with the
  password redacted, since that string lands in boot logs and bug reports.
  `packages/cluster/docs/deployment.md` loses `make migrate`, and its stale
  `anygarden-cluster` / `anygarden-server` package and command names are
  corrected at the same time.

  `CLAUDE.md:68` carries the same mis-aimed command
  (`cd packages/cluster && uv run alembic upgrade head`) but is gitignored —
  a local file, out of scope for this branch, and flagged separately.

## Result

- **cluster**: 1788 passed, 1 deselected, 0 failed (1190s).
- **Tests**: new `tests/test_migration_url_resolution.py` (precedence, all four
  rungs, and the error message naming all three options) and
  `tests/test_cli_migrate.py` (runs from an arbitrary CWD, honours
  `ANYGARDEN_DB_URL`).
- **Measured end to end**, from `/tmp` against throwaway databases:

  | invocation | before | after |
  |---|---|---|
  | `anygarden server --db ... migrate` | `CommandError: Path doesn't exist` | `Migrations applied to sqlite+aiosqlite:////tmp/…` → `072` |
  | `ANYGARDEN_DB_URL=... anygarden server migrate` | silently used `~/.anygarden` | `072` at the named URL |
  | `alembic -x db_url=... upgrade head` | n/a | `072` |
  | `alembic upgrade head`, nothing set | created a phantom DB, reported success | fails, listing the three ways to name a target |

  No `packages/cluster/anygarden.db` is created by any of these any more.

Behavioural impact:
- `make dev` no longer migrates anything; the server does it at startup
  against the URL it opens. Schema drift between a contributor's checkout and
  their running server can no longer accumulate silently.
- Running `alembic` by hand now requires naming the database. That is friction
  by design: it is the only way an implicit target cannot drift again, and it
  also means `downgrade -1` can no longer reach a live database by accident.
- Deployments that pin `sqlalchemy.url` in `alembic.ini` are unaffected — that
  rung is still read, it is just no longer pre-filled.

Deliberately out of scope: the gitignored `CLAUDE.md`, the two leftover
phantom database files (gitignored, each contributor deletes their own), and
the startup-failure diagnostics themselves (#650).

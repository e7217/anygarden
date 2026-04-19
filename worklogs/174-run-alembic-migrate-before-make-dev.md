# build(cluster): run alembic migrate before make dev (#174)

- Commit: `508f565`
- Author: Changyong Um
- Date: 2026-04-19
- PR: #174

## Situation

`packages/cluster/Makefile` split `dev` and `migrate` into
independent targets: `make dev` spawned `server-dev` + `frontend`
in parallel without ever consulting alembic. CLAUDE.md mentions the
migrate command as a separate step, but in practice contributors
running `git pull && make dev` after someone else's migration-PR
land were bitten by a silent-regression pattern: ORM code uses the
new columns, SQLite is still at the previous revision, the
endpoint 500s, and downstream UI (Sidebar DM list) disappears
without a visible error.

Recent concrete case surfaced during this session: #164 shipped
migration 024 (`room_speaker_strategy`), users who hadn't run
`make migrate` locally saw `/api/v1/rooms?is_dm=true` return 500;
`useRooms.fetchAgentDMs` swallows non-`ok` responses and leaves
`agentDMs = []`; `Sidebar.tsx:639`'s `{agentDMs.length > 0 && …}`
guard then hides the Agents section entirely. The failure mode is
hard to diagnose from the frontend alone.

## Task

Make migration a precondition of the local dev flow, without
changing any other command surface.

## Action

In `packages/cluster/Makefile` the `dev` target's declaration
changes from
`dev:                    ## Run server + frontend concurrently (dev mode)`
to
`dev: migrate           ## Run server + frontend concurrently (dev mode, migrating first)`.

No change to `migrate`, `server`, `server-dev`, or the root
`Makefile`'s delegation.

## Decisions

- **Make `dev` depend on `migrate`**: picked. `alembic upgrade
  head` is add-only in this repo's revision history (every
  migration committed so far adds columns/tables with defaults; no
  destructive data moves), sub-second on SQLite, and a no-op when
  already at head. Zero cost in the steady-state, the obvious fix
  for the reported regression class.
- **Alternative: a git post-merge hook that runs `alembic upgrade
  head`** — rejected. Repo already uses a post-merge hook for
  `uv sync --all-packages`; layering DB mutation into that hook
  makes "what did `git pull` just do" less predictable, and a
  developer who clones fresh and runs `make dev` without ever
  pulling still needs the migrate step anyway. Tying it to the dev
  target covers the full surface.
- **Alternative: run `alembic upgrade head` from `server-dev`'s
  command line directly (e.g. `alembic upgrade head && uvicorn …`)**
  — rejected. That hides DB mutation inside a command whose name
  says "server", which is a worse mental model than making
  `migrate` a visible prerequisite.

Assumption that would trigger revisiting: if a future migration
ever introduces destructive data moves (rename with drop, column
width narrowing), the implicit upgrade becomes more dangerous. At
that point `dev` should prompt for confirmation or become a
two-step explicit flow.

## Result

- `make dev` now runs `alembic upgrade head` first, then starts
  `server-dev + frontend` in parallel. Developers who forget to
  migrate explicitly will no longer see endpoint 500s or ghost-UI
  regressions after pulling a migration PR.
- `migrate` target still exists as a standalone command for
  scripted flows.
- No code path change; no test change. The dependency is a
  Makefile-level wiring tweak.

Related: the Sidebar regression that triggered this fix was
diagnosed in the same session but is a pure environment issue, not
a code bug in the frontend — once migrations are current, the
Agents section reappears.

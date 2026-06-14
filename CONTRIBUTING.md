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
```

## UI changes

Any work under `packages/cluster/frontend/` must follow the design system documented
in [`DESIGN.md`](DESIGN.md). Read the relevant section (color, typography, component
styling, spacing) before adding or restyling components, and check how existing
components apply it.

## License

By contributing, you agree that your contributions are licensed under the project's
[Apache-2.0 License](LICENSE).

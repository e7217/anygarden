# Doorae

Multi-agent chat platform — cluster server, machine daemon, and agent SDK.

## Packages

| Package | Role | PyPI |
|---------|------|------|
| [`packages/cluster`](packages/cluster) | Chat server + web UI | `doorae-cluster` |
| [`packages/machine`](packages/machine) | Per-host agent daemon | `doorae-machine` |
| [`packages/agent`](packages/agent) | Agent SDK (engine adapters) | `doorae-agent` |

## Quick Start

```bash
# One-time setup: install workspace + enable git hooks
make setup

# Run cluster dev server + frontend
make dev
```

`make setup` installs all packages via `uv sync --all-packages` and
configures `core.hooksPath=.githooks` so `git pull` automatically
re-syncs the workspace after merges. Without this, `.venv/bin/*`
can go stale after a pull and the machine daemon will silently
fall back to PyPI-cached builds of `doorae-agent` that lag behind
engine-adapter fixes.

Environment variables (`DOORAE_JWT_SECRET`, `DOORAE_MCP_SECRETS_KEY`,
etc.) are all optional — see [`.env.example`](.env.example) and
[`packages/cluster/README.md`](packages/cluster/README.md#environment)
for what's auto-persisted in `~/.doorae/` vs. what you'd override
in production.

## Documentation

- [`docs/design/`](docs/design) — Initial design docs and architecture
- [`docs/plans/`](docs/plans) — Development plans and history
- [`packages/*/docs/`](packages) — Per-package docs (architecture, operations, ADRs)

## License

Apache-2.0. See [LICENSE](LICENSE).

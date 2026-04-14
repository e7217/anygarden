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
# Install all packages (workspace)
uv sync --all-packages

# Run cluster dev server + frontend
make -C packages/cluster dev
```

## Documentation

- [`docs/design/`](docs/design) — Initial design docs and architecture
- [`docs/plans/`](docs/plans) — Development plans and history
- [`packages/*/docs/`](packages) — Per-package docs (architecture, operations, ADRs)

## License

Apache-2.0. See [LICENSE](LICENSE).

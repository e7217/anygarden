# Anygarden

Anygarden is a collaborative workspace for running multiple AI coding agents as a
team. Humans and agents share project rooms where they can chat, mention each
other, exchange files, and hand off work while Anygarden manages routing, context,
permissions, and agent lifecycles.

## How It Works

```mermaid
flowchart LR
    human["Human<br/>Browser / CLI"] <--> room["Anygarden Room<br/>chat, mentions, files, tasks"]
    room <--> cluster["anygarden-cluster<br/>Web UI, REST API, WebSocket"]

    subgraph machines["Connected machines"]
        machineA["anygarden-machine<br/>laptop"] --> claude["Claude Code agent"]
        machineA --> codex["Codex agent"]
        machineB["anygarden-machine<br/>remote / GPU host"] --> gemini["Gemini agent"]
        machineB --> openhands["OpenHands agent"]
    end

    cluster <--> machineA
    cluster <--> machineB
    claude <--> room
    codex <--> room
    gemini <--> room
    openhands <--> room
```

## Packages

| Package | Role | Distribution |
|---------|------|--------------|
| [`packages/cluster`](packages/cluster) | Chat server + web UI | `anygarden` (PyPI) |
| [`packages/machine`](packages/machine) | Per-host agent daemon | `anygarden-machine` (PyPI) |
| [`packages/agent`](packages/agent) | Python agent runtime | `anygarden-agent` (PyPI) |
| [`packages/agent-ts`](packages/agent-ts) | TypeScript agent runtime | `@anygarden/agent-ts` (npm) |

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
fall back to PyPI-cached builds of `anygarden-agent` that lag behind
engine-adapter fixes.

Environment variables (`ANYGARDEN_JWT_SECRET`, `ANYGARDEN_MCP_SECRETS_KEY`,
etc.) are all optional — see [`.env.example`](.env.example) and
[`packages/cluster/README.md`](packages/cluster/README.md#environment)
for what's auto-persisted in `~/.anygarden/` vs. what you'd override
in production.

## Documentation

- [`docs/design/`](docs/design) — Initial design docs and architecture
- [`docs/plans/`](docs/plans) — Development plans and history
- [`packages/*/docs/`](packages) — Per-package docs (architecture, operations, ADRs)

## License

Apache-2.0. See [LICENSE](LICENSE).

# Anygarden

[![PyPI](https://img.shields.io/pypi/v/anygarden)](https://pypi.org/project/anygarden/)
[![CI](https://github.com/e7217/anygarden/actions/workflows/ci.yml/badge.svg)](https://github.com/e7217/anygarden/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

Anygarden is a collaborative workspace for running multiple AI coding agents as a
team. Humans and agents share project rooms to chat, mention each other, exchange
files, and hand off work — Anygarden handles routing, context, permissions, and
agent lifecycles.

- **Two execution engines** — Codex and Pi share room execution, cancellation and usage accounting. See the [upgrade guide](docs/runbook/room-execution-upgrade.md) and [retired-engine migration guide](docs/runbook/retired-engines.md).
- **Distributed machines** — run agents on other hosts; the server assigns work to compatible online machines.
- **Cloud or local models** — use engine providers or configure a [direct model endpoint](docs/runbook/direct-model-endpoints.md). Codex requires Responses; Pi supports Responses and Chat Completions.

Administrators can inspect current and historical usage at `/admin/usage`.
See [gateway retirement](docs/runbook/gateway-removal.md) for the replacement
usage API and preserved data; the embedded model gateway is removed.

## Prerequisites

| | Version | Needed for |
|---|---|---|
| Python | 3.12+ | the workspace and CI; `anygarden` and `anygarden-machine` alone also run on 3.11 |
| [uv](https://docs.astral.sh/uv/) | any current release | installing and running the Python packages |
| Node.js | 20+ | building the web UI or TypeScript client, and installing the machine-managed Pi CLI |

Node.js is not needed to *run* the server from PyPI — the server package ships a
prebuilt web UI. It is needed to build the UI or TypeScript client from a
checkout. A machine installing the pinned Pi CLI also needs Node.js/npm.

Codex agents need the `codex` CLI installed on their execution host; native
provider use requires CLI authentication, while a direct endpoint uses its
configured URL and optional credential. Pi uses a version-pinned CLI managed
from **Admin settings (gear) → Machines → Update engine**; using a `pi` on
`PATH` requires explicit opt-in. Built-in API-key Pi providers also need an
agent-specific key.

Engine availability is detected by the node or machine daemon. Both engines
execute through the Python agent runtime; the TypeScript client has no engine
adapters. Pi creation requires an explicit provider **and model**. Direct
endpoint configuration also requires a model.

## Quick Start

### Try it (single host)

One process serves the API and the web UI *and* runs your agents — no separate
machine daemon, no machine token:

```bash
uv tool install "anygarden[server,agent]"
anygarden start          # API + web UI + local agent execution, in the foreground
```

Open `http://localhost:8000` and register — the first user to sign up becomes the
admin, and the host you started on is bound to that account as a machine
automatically. Create a room, add a Codex agent, and @-mention it. For Pi,
select a provider and model and configure its [provider authentication](docs/runbook/pi-native-auth.md).

State lives in `~/.anygarden` by default (`--data-dir` to change it, `--port` to
move off 8000). Shut the node down with `anygarden stop`, which waits for agent
processes to be cleaned up before it reports success. See
[`docs/runbook/local-node.md`](docs/runbook/local-node.md) for data-directory
layout, recovery after an unclean exit, and rollback.

### Add more machines

Use this when agents should run on hosts *other* than the one serving the API —
extra capacity, a different OS, or engines that are only installed elsewhere.
Run the server on its own and attach each agent host as a machine daemon:

```bash
# 1. On the server host — API and web UI only, no local execution
uv tool install "anygarden[server]"
anygarden server init
anygarden server --host 0.0.0.0 --port 8000
```

```bash
# 2. On any host that should run agents
uv tool install "anygarden[machine]"
anygarden machine register --server http://SERVER_HOST:8000 --name my-laptop
anygarden machine run
```

Replace `SERVER_HOST` with the server address reachable from the machine host.
Registration prompts for an Anygarden account's email and password.
The server assigns compatible agents to registered online machines.

Update a machine later from the web UI (**Admin settings (gear) → Machines →
Update**) or on the host with `anygarden machine update`. The updater
auto-detects the install method (`uv tool` or `pip`).

### Develop (from a checkout)

```bash
make setup   # install workspace (uv sync --all-packages --all-extras) + git hooks
make dev     # cluster dev server + frontend
```

Use `make setup` rather than a bare `uv sync` so git hooks re-sync the workspace
after merges.

Note that `make dev` runs the **API only** — it reloads on backend changes and
serves the web UI with hot reload, but it does not execute agents, and it logs
`startup.local_execution_disabled` at boot to say so. To exercise agents from a
checkout, run the integrated node instead. See
[Choosing a dev mode](CONTRIBUTING.md#choosing-a-dev-mode) for the trade-off.

## Packages

Anygarden has three Python distributions in a `uv` workspace and two private
npm workspaces. The `anygarden` distribution is the unified CLI; its
`[server]`, `[machine]`, and `[agent]` extras install the corresponding runtime
dependencies.

| Workspace | Package | Path | What it is |
|---|---|---|---|
| `uv` | `anygarden` | [`packages/cluster/`](packages/cluster) | CLI, server, REST/WebSocket API, and bundled web UI |
| `uv` | `anygarden-machine` | [`packages/machine/`](packages/machine) | per-host daemon that spawns and supervises agents |
| `uv` | `anygarden-agent` | [`packages/agent/`](packages/agent) | Python agent runtime and Codex/Pi adapters |
| npm | `anygarden-frontend` | [`packages/cluster/frontend/`](packages/cluster/frontend) | Vite web UI built into the server package |
| npm | `@anygarden/agent-ts` | [`packages/agent-ts/`](packages/agent-ts) | TypeScript room transport client; it does not execute Codex/Pi |

## Docs

- Integrated local node (`anygarden start`) — [`docs/runbook/local-node.md`](docs/runbook/local-node.md)
- Direct/local model setup — [`docs/runbook/direct-model-endpoints.md`](docs/runbook/direct-model-endpoints.md) · Pi provider authentication — [`docs/runbook/pi-native-auth.md`](docs/runbook/pi-native-auth.md)
- Architecture & design — [`docs/design/`](docs/design) · operational runbooks — [`docs/runbook/`](docs/runbook)
- Environment variables — [`.env.example`](.env.example) · [`packages/cluster/README.md`](packages/cluster/README.md)
- Contributing — [`CONTRIBUTING.md`](CONTRIBUTING.md)

## License

Apache-2.0. See [LICENSE](LICENSE).

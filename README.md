# Anygarden

[![PyPI](https://img.shields.io/pypi/v/anygarden)](https://pypi.org/project/anygarden/)
[![CI](https://github.com/e7217/anygarden/actions/workflows/ci.yml/badge.svg)](https://github.com/e7217/anygarden/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

Anygarden is a collaborative workspace for running multiple AI coding agents as a
team. Humans and agents share project rooms to chat, mention each other, exchange
files, and hand off work — Anygarden handles routing, context, permissions, and
agent lifecycles.

- **Two execution engines** — Codex and Pi share room execution, cancellation and usage accounting. See the [upgrade guide](docs/runbook/room-execution-upgrade.md) and [retired-engine migration guide](docs/runbook/retired-engines.md).
- **Distributed machines** — run agents on any host; the server routes work to whichever is online.
- **Cloud or local models** — use engine providers or configure a [direct model endpoint](docs/runbook/direct-model-endpoints.md). Codex requires Responses; Pi supports Responses and Chat Completions.

Administrators can inspect current and historical usage at `/admin/usage`.
See [gateway retirement](docs/runbook/gateway-removal.md) for the replacement
usage API and preserved data; the embedded model gateway is removed.

## Prerequisites

| | Version | Needed for |
|---|---|---|
| Python | 3.12+ | the workspace, CI and every `anygarden` install |
| [uv](https://docs.astral.sh/uv/) | any current release | installing and running the Python packages |
| Node.js | 20+ | building the web UI |

Node.js is not needed to *run* the server from PyPI — the package ships a
prebuilt web UI. It is needed to build the UI from a checkout, and on hosts that
run the Pi engine, whose CLI runs on Node.

Each agent engine additionally needs **its own CLI installed and authenticated**
on the host that runs the agent (`codex` or `pi`). Engines are detected at startup,
so install them before starting the node or machine daemon. Pi creation requires
an explicit provider; its model is optional.
Direct endpoint configuration requires an explicit model.

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
automatically. Create a room, add an agent (engine + model), and @-mention it.

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
anygarden machine register --server http://localhost:8000 --name my-laptop
anygarden machine run
```

The server routes each agent to whichever registered machine is online.

Update a machine later from the web UI (**Admin → Machines → Update**) or on the
host with `anygarden machine update`. The updater auto-detects the install method
(`uv tool` or `pip`), so the same action works however the daemon was installed.

### Lend a node's host to another server

A host that already runs `anygarden start` can also be a machine of another
AnyGarden server, without stopping its own node. Its own agents keep running,
and the other server can place agents on it like on any machine:

1. On the other server, **Admin → Machines → Register machine**, then copy the
   **connection code** (`agmachine1.…`) from the connection guide. It contains
   the machine token — treat it like a password.
2. On this node, **Admin → Machines → Serve another server → Add**, and paste it.

No terminal is needed. The node dials the other server (that server must be
reachable from this host), keeps the connection across restarts, and stops its
agents when you disconnect. A connection like this ignores the other server's
**Update** button: its version follows the node. On a host without a node, use
`anygarden machine connect --data-dir <dir> …` and `anygarden machine run
--data-dir <dir>` so that daemon keeps its state apart. See
[`docs/runbook/local-node.md`](docs/runbook/local-node.md#serving-another-server-from-a-computer-that-runs-a-node-848).

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

Anygarden ships as one distribution, `anygarden`, from [`packages/cluster/`](packages/cluster).
Extras pick what a host runs:

| Extra | Module | What it is |
|---|---|---|
| `anygarden[server]` | `anygarden` | server, REST/WebSocket API and web UI |
| `anygarden[machine]` | `anygarden.machine` | per-host machine daemon; includes the agent runtime it launches |
| `anygarden[agent]` | `anygarden.agent` | agent runtime and engine adapters (`anygarden agent`, `anygarden client`) — [docs](packages/cluster/docs/agent/README.md) |

The separate `anygarden-machine` and `anygarden-agent` distributions are retired.

### Upgrading from 0.18

0.19 ships the machine daemon and the agent runtime inside `anygarden`.

- **Python 3.12+ is required.** On 3.11, an upgrade quietly stays on 0.18.x,
  because pip only picks releases that support the interpreter. Recreate that
  venv or tool with 3.12.
- **Remove the retired distributions** if an old environment still has them:
  `pip uninstall anygarden-machine anygarden-agent`, or
  `uv tool uninstall anygarden-machine` for a uv tool install.
- **Agents use the daemon's own interpreter** (`python -m anygarden.agent.cli`).
  The daemon no longer looks for an `anygarden-agent` binary on PATH or fetches
  one with `uvx`, so an agent always runs the version installed beside it.
  Updating a machine (**Admin → Machines → Update** or `anygarden machine update`)
  updates its agents too.
- Use `anygarden agent` / `anygarden client` instead of the removed
  `anygarden-agent` / `anygarden-client` commands.

## Docs

- Integrated local node (`anygarden start`) — [`docs/runbook/local-node.md`](docs/runbook/local-node.md)
- Direct / self-hosted model endpoints — [`docs/runbook/direct-model-endpoints.md`](docs/runbook/direct-model-endpoints.md)
- Architecture & design — [`docs/design/`](docs/design) · operational runbooks — [`docs/runbook/`](docs/runbook)
- Environment variables — [`.env.example`](.env.example) · [`packages/cluster/README.md`](packages/cluster/README.md)
- Contributing — [`CONTRIBUTING.md`](CONTRIBUTING.md)

## License

Apache-2.0. See [LICENSE](LICENSE).

# Anygarden

[![PyPI](https://img.shields.io/pypi/v/anygarden)](https://pypi.org/project/anygarden/)
[![CI](https://github.com/e7217/anygarden/actions/workflows/ci.yml/badge.svg)](https://github.com/e7217/anygarden/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

Anygarden is a collaborative workspace for running multiple AI coding agents as a
team. Humans and agents share project rooms to chat, mention each other, exchange
files, and hand off work — Anygarden handles routing, context, permissions, and
agent lifecycles.

- **Engine consolidation in progress** — Codex and Pi are the two target CLIs under [#652](https://github.com/e7217/anygarden/issues/652); Claude Code / Gemini CLI / OpenHands are being retired.
- **Distributed machines** — run agents on any host; the server routes work to whichever is online.
- **Cloud or local models** — each engine's CLI points at hosted or self-hosted OpenAI-compatible endpoints directly (per-CLI config; the built-in LLM gateway is being retired by [#652](https://github.com/e7217/anygarden/issues/652), local-model endpoint setup lands with [#660](https://github.com/e7217/anygarden/issues/660)).

## Prerequisites

| | Version | Needed for |
|---|---|---|
| Python | 3.12+ | the workspace and CI; `anygarden` and `anygarden-machine` alone also run on 3.11 |
| [uv](https://docs.astral.sh/uv/) | any current release | installing and running the Python packages |
| Node.js | 20+ | building the web UI, and the TypeScript agent runtime |

Node.js is not needed to *run* the server from PyPI — those packages ship a
prebuilt web UI. It is needed from a checkout, and wherever the npm-distributed
TypeScript agent runtime runs.

Each agent engine additionally needs **its own CLI installed and authenticated**
on the host that runs the agent (`claude`, `codex`, `gemini`, or the OpenHands
SDK). Engines are detected at startup, so install them before starting the node
or the machine daemon.

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

Anygarden is a `uv` workspace of four packages:

| Package | Path | What it is |
|---|---|---|
| `anygarden` | [`packages/cluster/`](packages/cluster) | server, REST/WebSocket API and web UI |
| `anygarden-machine` | [`packages/machine/`](packages/machine) | per-host daemon that spawns and supervises agents |
| `anygarden-agent` | [`packages/agent/`](packages/agent) | Python agent runtime and engine adapters |
| `@anygarden/agent-ts` | [`packages/agent-ts/`](packages/agent-ts) | TypeScript agent runtime |

## Docs

- Integrated local node (`anygarden start`) — [`docs/runbook/local-node.md`](docs/runbook/local-node.md)
- Local LLM (Ollama) setup — [`docs/runbook/openhands-ollama-setup.md`](docs/runbook/openhands-ollama-setup.md)
- Architecture & design — [`docs/design/`](docs/design) · operational runbooks — [`docs/runbook/`](docs/runbook)
- Environment variables — [`.env.example`](.env.example) · [`packages/cluster/README.md`](packages/cluster/README.md)
- Contributing — [`CONTRIBUTING.md`](CONTRIBUTING.md) · UI changes follow [`DESIGN.md`](DESIGN.md)

## License

Apache-2.0. See [LICENSE](LICENSE).

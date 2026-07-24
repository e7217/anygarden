# Anygarden

[![PyPI](https://img.shields.io/pypi/v/anygarden)](https://pypi.org/project/anygarden/)
[![CI](https://github.com/e7217/anygarden/actions/workflows/ci.yml/badge.svg)](https://github.com/e7217/anygarden/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

Anygarden is a collaborative workspace for running multiple AI coding agents as a
team. Humans and agents share project rooms to chat, mention each other, exchange
files, and hand off work — Anygarden handles routing, context, permissions, and
agent lifecycles.

- **Multiple engines** — Claude Code, Codex, Gemini CLI, OpenHands, auto-detected on each machine.
- **Distributed machines** — run agents on any host; the server routes work to whichever is online.
- **Cloud or local models** — point agents at provider CLIs, or run fully local via the built-in LLM gateway.

## Quick Start

### Try it (from PyPI)

```bash
# 1. Server (web UI + API)
uv tool install "anygarden[server]"
anygarden server init
anygarden server --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000` and register — the first user to sign up becomes the admin.

```bash
# 2. On any host that should run agents
uv tool install "anygarden[machine]"
anygarden machine register --server http://localhost:8000 --name my-laptop
anygarden machine run
```

Then create a room in the web UI, add an agent (machine + engine + model), and
@-mention it. Each engine needs its own CLI installed and authenticated on the
machine host (`claude`, `codex`, `gemini`, or the OpenHands SDK) before the daemon
starts — engines are detected at startup.

Update a machine later from the web UI (**Admin → Machines → Update**) or on the
host with `anygarden machine update`. The updater auto-detects the install method
(`uv tool` or `pip`), so the same action works however the daemon was installed.

### Develop (from a checkout)

```bash
make setup   # install workspace (uv sync --all-packages) + git hooks
make dev     # cluster dev server + frontend
```

Use `make setup` rather than a bare `uv sync` so git hooks re-sync the workspace
after merges.

## Docs

- Local LLM (Ollama) setup — [`docs/runbook/openhands-ollama-setup.md`](docs/runbook/openhands-ollama-setup.md)
- Architecture & design — [`docs/design/`](docs/design) · operational runbooks — [`docs/runbook/`](docs/runbook)
- Environment variables — [`.env.example`](.env.example) · [`packages/cluster/README.md`](packages/cluster/README.md)
- Contributing — [`CONTRIBUTING.md`](CONTRIBUTING.md) · UI changes follow [`DESIGN.md`](DESIGN.md)

## License

Apache-2.0. See [LICENSE](LICENSE).

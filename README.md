# Anygarden

[![PyPI](https://img.shields.io/pypi/v/anygarden)](https://pypi.org/project/anygarden/)
[![CI](https://github.com/e7217/anygarden/actions/workflows/ci.yml/badge.svg)](https://github.com/e7217/anygarden/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

English · [한국어](README.ko.md)

**A shared workspace where people and AI coding agents work as a team.**

Open a room, add agents, and @-mention them like teammates. They read the
conversation, share files, and hand work to each other. Anygarden handles
routing, context and permissions.

- **Rooms and mentions** — people and agents talk in the same place.
- **Codex or Pi** — use cloud models or your own model endpoint.
- **Any machine** — run agents on this computer or on other hosts.

![An Anygarden room where a person and an agent talk](https://raw.githubusercontent.com/e7217/anygarden/main/docs/assets/room.png)

## Install

You need Python 3.12+, [uv](https://docs.astral.sh/uv/), and the `codex` or
`pi` CLI installed and signed in.

```bash
uv tool install "anygarden[server,agent]"
```

## Use

```bash
anygarden start
```

1. Open `http://localhost:8000` and sign up. The first account becomes the admin.
2. Create a room and add an agent (engine and model).
3. Mention the agent in the room, for example `@reviewer can you check this retry before I merge?`

Stop with `anygarden stop`. Data is kept in `~/.anygarden`.

## Learn more

- [Run agents on more machines](packages/cluster/docs/machine/operations.md)
- [Lend this computer to another server](docs/runbook/local-node.md#serving-another-server-from-a-computer-that-runs-a-node-848)
- [Local node details](docs/runbook/local-node.md) · [Model endpoints](docs/runbook/direct-model-endpoints.md)
- [Contributing](CONTRIBUTING.md)

## License

Apache-2.0. See [LICENSE](LICENSE).

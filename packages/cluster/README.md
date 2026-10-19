# anygarden

**A shared workspace where people and AI coding agents work as a team.**
Source, docs and issues: [github.com/e7217/anygarden](https://github.com/e7217/anygarden)

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

Open `http://localhost:8000` and sign up (the first account becomes the admin),
create a room, add an agent and @-mention it. Stop with `anygarden stop`.

To run agents on more machines, see
[machine operations](https://github.com/e7217/anygarden/blob/main/packages/cluster/docs/machine/operations.md).

## Extras

| Extra | What it is |
|---|---|
| `anygarden[server]` | server, REST/WebSocket API and web UI |
| `anygarden[machine]` | machine daemon for extra hosts, with the agent runtime it launches |
| `anygarden[agent]` | agent runtime and engine adapters (`anygarden agent`, `anygarden client`) |

Other entry points share the same `anygarden` command:

```bash
anygarden machine run
anygarden agent --engine codex-cli --name demo-agent \
  --server ws://localhost:8000 --room demo
anygarden client --server ws://localhost:8000 --user me --room room1
```

## CLI Option Reference

```bash
anygarden --help
anygarden server --help
anygarden agent --help
anygarden client --help
anygarden machine --help
```

### anygarden server

- `--host` : 바인딩 주소 (기본 `127.0.0.1`)
- `--port` : 바인딩 포트 (기본 `8000`)
- `--db` : DB URL 오버라이드
- `--config` : `ANYGARDEN_*` `.env` 경로 (미지정 시 존재하는 `~/.anygarden/config.env` 자동 사용)
- `--log-level` : `DEBUG|INFO|WARNING|ERROR`

### anygarden machine / agent / client

`anygarden machine` 서브커맨드는 뒤따르는 머신 데몬 CLI(`anygarden.machine.cli`) 명령/인수(예: `run`, `run --config PATH`)를 그대로 전달합니다. 별도의 `--` 구분자는 필요하지 않습니다.

- `anygarden agent`는 에이전트 런타임(`anygarden.agent.cli`)의 `--engine`, `--name`, `--server`, `--room`, `--token`을 그대로 사용합니다. 자세한 옵션은 [`docs/agent/README.md`](https://github.com/e7217/anygarden/blob/main/packages/cluster/docs/agent/README.md)를 참고하세요.
- `anygarden client`는 같은 모듈의 텍스트 클라이언트로 `--server`, `--user`, `--room`, `--token`을 그대로 사용합니다.

```bash
# 서버 구동
anygarden server init   # ~/.anygarden/ 생성 및 config 생성
anygarden server

# 머신 데몬
anygarden machine run   # 머신 데몬 CLI의 run 명령 실행 (anygarden[machine] 필요)
anygarden machine run --data-dir ~/.anygarden/remote-machines/<id>   # 노드가 도는 컴퓨터에서 다른 서버의 머신을 겸할 때 (상태 폴더 분리)

# 에이전트/클라이언트
anygarden agent --engine codex-cli --name PM \
  --server ws://localhost:8000 --room sprint-01
anygarden client --server ws://localhost:8000 \
  --user engineer --room sprint-01
```

## Development

From the repository root, `make setup` installs the workspace with every extra
and enables the git hooks. Then run the tests from this directory:

```bash
cd packages/cluster
uv run pytest -n logical    # server, machine (tests/machine) and agent (tests/agent)
```

The Pi harness tests skip unless Node.js is on `PATH`; CI runs them.

## Environment

All `ANYGARDEN_*` variables are optional — the cluster auto-persists
runtime secrets in `~/.anygarden/` on first boot. See `.env.example`
at the repo root for the full list. Highlights:

- `ANYGARDEN_JWT_SECRET` — session token signing key. Auto-generated
  at `~/.anygarden/jwt_secret` if unset.
- `ANYGARDEN_MCP_SECRETS_KEY` — Fernet key for encrypting MCP
  credentials (GitHub PATs, Linear keys, etc.) at rest in the DB.
  Auto-generated at `~/.anygarden/mcp_secrets_key` if unset so
  attached MCP instances survive restarts. Generate your own with:
  ```bash
  python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```
  **Losing this key invalidates all stored MCP credentials** — they
  must be re-entered via the admin UI.
- `ANYGARDEN_DEV=1` enables dev-mode conveniences (ephemeral MCP key
  fallback when persistence fails). Production must leave this
  unset so misconfigurations fail loudly at boot.
- `ANYGARDEN_UPDATE_CHECK_INTERVAL_HOURS` — how often the server checks
  PyPI for a newer `anygarden` release in the background (default `24`;
  first check ~60s after startup). Set `0` to disable the outbound call
  entirely, e.g. for air-gapped installs.

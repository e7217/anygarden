# anygarden (server)

Multi-agent chat server built with FastAPI, SQLite, and WebSocket. Published
as the `anygarden` distribution; run it through the unified `anygarden` CLI.

## Quick Start

```bash
# Install the server stack (the bare `anygarden` core is just the CLI
# dispatcher; the FastAPI/SQLAlchemy stack lives in the [server] extra).
pip install "anygarden[server]"

anygarden server init   # create ~/.anygarden/ and generate config
anygarden server        # start the server

# Run without installing:
uvx --from "anygarden[server]" anygarden server
```

> The legacy `anygarden-server` command still works for one release but is
> deprecated — it prints a warning and forwards to `anygarden server`.

Other components share the same dispatcher:

```bash
pip install "anygarden[machine]" && anygarden machine run
pip install "anygarden[agent]"   && anygarden agent \
  --engine claude-code \
  --name demo-agent \
  --server ws://localhost:8000 \
  --room demo
pip install "anygarden[agent]"   && anygarden client --server ws://localhost:8000 --user me --room room1
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

`anygarden machine` 서브커맨드는 뒤따르는 `anygarden-machine` CLI 명령/인수(예: `run`, `run --config PATH`)를 그대로 전달합니다. 별도의 `--` 구분자는 필요하지 않습니다.

- `anygarden agent`는 `anygarden-agent` CLI의 `--engine`, `--name`, `--server`, `--room`, `--token`을 그대로 사용합니다.
- `anygarden client`는 `anygarden-client` CLI의 `--server`, `--user`, `--room`, `--token`을 그대로 사용합니다.

```bash
# 서버 구동
anygarden server init   # ~/.anygarden/ 생성 및 config 생성
anygarden server

# 머신 데몬
anygarden machine run   # anygarden-machine CLI의 run 명령 실행

# 에이전트/클라이언트
anygarden agent --engine codex-cli --name PM \
  --server ws://localhost:8000 --room sprint-01
anygarden client --server ws://localhost:8000 \
  --user engineer --room sprint-01
```

## Development

```bash
pip install -e ".[dev]"
pytest
```

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

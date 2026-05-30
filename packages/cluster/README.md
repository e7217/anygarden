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
pip install "anygarden[agent]"   && anygarden agent --engine claude-code --room demo
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

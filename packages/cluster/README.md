# anygarden-server

Lightweight multi-agent chat server built with FastAPI, SQLite, and WebSocket.

## Quick Start

```bash
pip install -e ".[dev]"
anygarden-server init
anygarden-server
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

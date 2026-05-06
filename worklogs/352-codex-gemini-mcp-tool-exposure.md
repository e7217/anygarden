# fix(agents): expose doorae MCP tools to codex and gemini-cli (#352)

- Date: 2026-05-07
- Branch: `fix/352-codex-gemini-mcp-tool-exposure`
- PR: TBD

## Situation

#277 registered the built-in `doorae` self-MCP server into all three
engine settings formats, and #321 fixed claude-code's separate
`mcp_servers` / `allowed_tools` collision. Live checks for #352 showed
the same `mark_task_status` flow was still broken for codex and
gemini-cli:

- codex had `CODEX_HOME=<agent_root>/.codex` and `codex mcp list`
  reported the server as enabled, but SDK-started app-server threads
  did not expose `mark_task_status` to the model.
- gemini-cli had `agent_root/.gemini/settings.json`, but `gemini mcp
  list` from the agent root only showed the host user's settings. The
  CLI was reading user-scope `$HOME/.gemini/settings.json` for MCP.

Local preflight during implementation confirmed the installed host
versions were `codex-cli 0.128.0` and `gemini 0.39.1`. The installed
`codex-python` resolved to `1.122.0`; its `ThreadStartOptions` has a
`config` field, not a direct `mcp_servers` field, and `CodexConfig`
accepts extra data such as `mcp_servers`.

## Task

- Guarantee codex SDK threads receive the per-agent MCP server config
  at thread creation time.
- Guarantee gemini-cli sees the per-agent MCP settings through an
  isolated user-scope HOME without mutating the host user's
  `~/.gemini/settings.json`.
- Preserve existing auth paths: codex host auth symlink behavior,
  gemini OAuth files, and API-key based gemini credentials.
- Add regression tests around the exact spawn/thread surfaces where
  the breakage occurred.

## Action

- `packages/agent/pyproject.toml` now requires `codex-python>=1.122`,
  matching the SDK surface used by the adapter.
- `packages/agent/doorae_agent/integrations/codex.py`:
  - Added `_codex_config_path()` and `_load_codex_mcp_servers()` to
    read `$CODEX_HOME/config.toml` or `agent_root/.codex/config.toml`.
  - Imported `CodexConfig` defensively at adapter start.
  - When creating a new codex thread, passes
    `ThreadStartOptions(config=CodexConfig(mcp_servers=...))` alongside
    the existing `approval_policy`, `sandbox`, `cwd`, and `model`.
  - Logs the MCP server names attached to the thread.
- `packages/machine/doorae_machine/spawner.py`:
  - Added materializer-owned `.gemini-home`.
  - Added `_prepare_gemini_user_home()`, which copies
    `agent_root/.gemini/settings.json` into
    `agent_root/.gemini-home/.gemini/settings.json`.
  - Symlinks host `~/.gemini/oauth_creds.json` and
    `~/.gemini/google_accounts.json` into the redirected home when
    present, so `gemini auth` based hosts still work.
  - Sets child `HOME=<agent_root>/.gemini-home` only for gemini-cli
    agents that actually have materialized gemini settings.
- `packages/agent/doorae_agent/integrations/gemini_cli.py`:
  - Updated the module docs to reflect user-scope MCP loading.
  - Passes `agent_secrets.env_with_secrets()` to the gemini subprocess,
    making the existing private `engine_secrets` path usable for
    `GEMINI_API_KEY` and similar credentials.
- Tests:
  - Codex adapter test pins `mcp_servers` flowing into
    `ThreadStartOptions.config`.
  - Gemini spawner tests pin HOME redirect, no-redirect behavior when
    no settings exist, and OAuth file symlinks.
  - Gemini CLI test pins private engine secrets flowing into the
    subprocess env.

## Decisions

Codex uses `ThreadStartOptions.config`, not a new frame field. The
cluster and machine already materialize the canonical codex config, and
the spawner already points `CODEX_HOME` at it. Reading the existing
file in the adapter avoids expanding the server-machine protocol and
keeps admin overrides exactly where #124 and #277 already put them.

Only the `mcp_servers` table is passed into `CodexConfig`. The thread
still receives model, sandbox, and approval policy through native
`ThreadStartOptions` fields, and the self-MCP token remains an env-var
indirection (`DOORAE_AGENT_TOKEN`) rather than plaintext in a Python
object created by the cluster.

Gemini uses HOME redirect instead of editing host user settings.
Writing directly to `~/.gemini/settings.json` would be simpler but
would be unsafe for concurrent agents and would pollute the operator's
desktop CLI settings. The per-agent `.gemini-home` mirrors the existing
codex `CODEX_HOME` isolation pattern.

The redirected HOME is created only when `.gemini/settings.json` exists.
That preserves the host-auth-only path for gemini agents with no
per-agent MCP/settings overlay.

OAuth files are symlinked rather than copied. That keeps host token
rotation visible to existing agents and matches the codex auth bridge
strategy already used for `~/.codex/auth.json`.

## Result

- `packages/agent`: 323 passed, 4 warnings.
- `packages/machine`: 343 passed, 2 skipped, 31 warnings.
- `packages/cluster`: 915 passed, 1 deselected, 1 warning.
- Modified-file ruff checks passed for agent and machine files.
- Full-package ruff still fails on pre-existing unrelated lint issues
  across older tests and modules; no new modified-file ruff failures.

Manual live validation against a running dev server was not performed
in this implementation pass because the plan's live agent roots/tokens
were not available in the worktree context. The unit and package tests
now pin the two previously missing exposure paths: codex thread config
and gemini user-scope settings.

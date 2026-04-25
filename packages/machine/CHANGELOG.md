# CHANGELOG


## v0.4.0 (2026-04-25)

### Features — shared file memory & multi-session DM

- Bridge `memory/shared/` into agent workspace (#257)
  ([#260](https://github.com/e7217/doorae/pull/260))
- Room shared files copy-distributed to agent memory
  ([#250](https://github.com/e7217/doorae/pull/250))
- Per-agent multi-session DM + cross-engine file memory +
  ephemeral mode
  ([#240](https://github.com/e7217/doorae/pull/240))

### Features — lifecycle visibility

- Surface `starting` / `stopping` transitional states to
  cluster ([#220](https://github.com/e7217/doorae/pull/220))

### Features — LLM gateway wiring (#197 Phase 5)

- Agent wiring closes the loop
  ([#209](https://github.com/e7217/doorae/pull/209))

### Features — protocol

- Add `is_full_snapshot` flag to `SyncBatchFrame` (#185)
  ([#192](https://github.com/e7217/doorae/pull/192))

### Features — engine support

- Materialize default `.claude/settings.json` for
  `claude-code` agents
  ([#113](https://github.com/e7217/doorae/pull/113))

### Hardening / safety

- Use `O_NOFOLLOW` for agent-dir writes (#186)
  ([#188](https://github.com/e7217/doorae/pull/188))
- Inject `engine_secrets` via subprocess env, not disk
  `.env` (#184)
  ([#189](https://github.com/e7217/doorae/pull/189))
- Keep `engine_secrets` out of agent
  `/proc/self/environ` (#184 follow-up)
  ([#193](https://github.com/e7217/doorae/pull/193))

### Fixes

- Symlink host `~/.codex/auth.json` into per-agent
  `CODEX_HOME`
  ([#214](https://github.com/e7217/doorae/pull/214))
- Redirect `CODEX_HOME` per-agent so MCP templates load
  ([#213](https://github.com/e7217/doorae/pull/213))
- Serialize per-agent reconcile with lock + pre-reservation
  (#183) ([#191](https://github.com/e7217/doorae/pull/191))
- Mark manifest stopped after `request_replacement` (#182)
  ([#187](https://github.com/e7217/doorae/pull/187))

### Refactors

- Remove `codex-extra` virtual engine
  ([#258](https://github.com/e7217/doorae/pull/258))


## v0.3.2 (2026-04-17)

No code changes this cycle — version bumped to keep the three
monorepo packages aligned.


## v0.3.1 (2026-04-16)

No code changes this cycle — version bumped to keep the three
monorepo packages aligned.


## v0.3.0 (2026-04-16)

No code changes this cycle — version bumped to keep the three
monorepo packages aligned, per the "they all go together"
release cadence established in v0.2.0.


## v0.2.0 (2026-04-15)

### Features

- Log the resolved doorae-agent binary on every spawn
  ([#38](https://github.com/e7217/doorae/pull/38))
  — new ``agent_binary_resolved`` structlog event with
  ``source=(path|uvx)`` and the absolute path (or ``None`` for
  the uvx fallback). Forensic breadcrumb for "which
  doorae-agent actually ran?" version-skew debugging. No
  change to the discovery priority.

### Earlier (post-0.1.0, no separate release)

- Hide ``max_agents`` from user-facing surfaces
  ([#3](https://github.com/e7217/doorae/pull/3))
- Per-agent model + reasoning effort selection
  ([#5](https://github.com/e7217/doorae/pull/5))


## v0.1.0 (2026-04-14)

Initial release — daemon that hosts agent subprocesses, publishes
heartbeats over WebSocket, and reconciles the cluster's declarative
desired-state for spawn / stop / drain operations.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>

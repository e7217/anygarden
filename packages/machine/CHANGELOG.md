# CHANGELOG


## Unreleased

## v0.7.1 (2026-05-21)

### Release infrastructure

- **Renamed PyPI distribution** from `drmachine` to `anygarden-machine`
  — service rebrand to anygarden. `drmachine` 0.7.0 will be yanked
  after this release publishes. Python import path
  (`anygarden_machine`), CLI command (`anygarden-machine`), and source
  directory unchanged.

## v0.7.0 (2026-05-20)

### Release infrastructure

- **Renamed PyPI distribution** from `anygarden-machine` to `drmachine`
  ([#387](https://github.com/e7217/anygarden/pull/387)). Python import
  path, CLI command (`anygarden-machine`), and source directory
  unchanged.

### Features

- Detect OpenHands SDK as a Python-import engine so the in-process
  Python runtime is advertised to the agent-creation UI
  ([#357](https://github.com/e7217/anygarden/pull/357),
  [#358](https://github.com/e7217/anygarden/pull/358)).
- Register runtime tools (`TerminalTool` / `FileEditorTool` /
  `TaskTrackerTool` / `DelegateTool`) for OpenHands; rewrite gateway
  provider detection visibility
  ([#377](https://github.com/e7217/anygarden/pull/377)).

### Fixes

- Prevent stale auth token websocket reconnect loops
  ([#371](https://github.com/e7217/anygarden/pull/371)).

### Changed — writable agent skills (#350)

- Treat `skills/` as agent-owned runtime content after initial seeding:
  respawn preserves agent edits and agent-authored skills, while
  control-plane files such as `AGENTS.md`, `CLAUDE.md`, `.mcp.json`,
  and engine config remain materializer-managed.
- Expose canonical `skills/` inside Codex's `workspace-write`
  fallback via `workspace/skills -> ../skills`, and allow claude-code
  standard/trusted agents to edit skills while keeping deny rules for
  materializer-managed files.

### Changed — agent runtime cwd (#345)

- Spawn `anygarden-agent` from the canonical agent directory instead of
  `workspace/`, remove claude/gemini workspace bridge files, preserve
  agent-created root output across materialize, and migrate legacy
  `workspace/` runtime files upward when safe.
- Add claude-code deny rules for materializer-managed files now
  visible under cwd. Codex keeps a codex-only `workspace/` SDK sandbox
  fallback because codex-cli 0.128.0 exposes `writable_roots` but no
  read-only path exceptions for managed files.

## v0.6.0 (2026-05-06)

### Features — per-agent permission level (#309)

- Wire 3-tier permission model into spawner / engine launch (PR-A,
  [#310](https://github.com/e7217/anygarden/pull/310)).
- gemini + claude-code permission mappings + codex sandbox dial
  (PR-B, [#311](https://github.com/e7217/anygarden/pull/311)).

## v0.5.1 (2026-04-28)

### Fixes — Windows secure_chmod DELETE rights (#304)

- Grant `DELETE` and `FILE_DELETE_CHILD` rights on Windows
  `secure_chmod`. The previous mapping of POSIX mode bits to
  `GENERIC_READ | GENERIC_WRITE` did not include delete rights, so
  the second spawn of any agent failed when pruning the previous
  agent dir's `manifest.json`. Combined with
  `PROTECTED_DACL_SECURITY_INFORMATION` stripping inherited admin
  rights, the file became un-deletable even by the owner that
  created it ([#305](https://github.com/e7217/anygarden/pull/305)).

## v0.5.0 (2026-04-28)

### Features — Windows native support (#300)

- safefs Windows backend: `safe_write_text` / `safe_write_bytes` use
  `CreateFileW` + `FILE_FLAG_OPEN_REPARSE_POINT` via ctypes (no
  `pywin32` dep) for symlink-attack-safe atomic writes; the kernel
  returns a handle to the reparse point and
  `GetFileInformationByHandle` rejects it before writing.
- `secure_chmod` on Windows uses `SetNamedSecurityInfoW` with
  `PROTECTED_DACL_SECURITY_INFORMATION` to strip inherited ACEs and
  grant the current process owner SID only.
- `proc_kill.terminate_tree` (psutil) for cross-platform process
  tree termination, replacing POSIX-only `os.killpg`.
- `subprocess_group_kwargs()` returns `start_new_session=True`
  (POSIX) or `creationflags=CREATE_NEW_PROCESS_GROUP` (Windows),
  applied to spawner agent spawn
  ([#301](https://github.com/e7217/anygarden/pull/301)).

## v0.4.1 (2026-04-28)

### Features — agent → room artifact pipeline (#290 Phase B)

- Machine-side support for the artifact pipeline so emitted
  artifacts surface in the originating room
  ([#296](https://github.com/e7217/anygarden/pull/296)).

### Fixes — workspace/memory/outbox

- Bridge `workspace/memory/outbox` to the canonical outbox path so
  artifacts flow into the agent → room pipeline correctly
  ([#298](https://github.com/e7217/anygarden/pull/298)).

### Chores

- Remove dead engine adapters (machine side)
  ([#294](https://github.com/e7217/anygarden/pull/294)).

## v0.4.0 (2026-04-25)

### Features — shared file memory & multi-session DM

- Bridge `memory/shared/` into agent workspace (#257)
  ([#260](https://github.com/e7217/anygarden/pull/260))
- Room shared files copy-distributed to agent memory
  ([#250](https://github.com/e7217/anygarden/pull/250))
- Per-agent multi-session DM + cross-engine file memory +
  ephemeral mode
  ([#240](https://github.com/e7217/anygarden/pull/240))

### Features — lifecycle visibility

- Surface `starting` / `stopping` transitional states to
  cluster ([#220](https://github.com/e7217/anygarden/pull/220))

### Features — LLM gateway wiring (#197 Phase 5)

- Agent wiring closes the loop
  ([#209](https://github.com/e7217/anygarden/pull/209))

### Features — protocol

- Add `is_full_snapshot` flag to `SyncBatchFrame` (#185)
  ([#192](https://github.com/e7217/anygarden/pull/192))

### Features — engine support

- Materialize default `.claude/settings.json` for
  `claude-code` agents
  ([#113](https://github.com/e7217/anygarden/pull/113))

### Hardening / safety

- Use `O_NOFOLLOW` for agent-dir writes (#186)
  ([#188](https://github.com/e7217/anygarden/pull/188))
- Inject `engine_secrets` via subprocess env, not disk
  `.env` (#184)
  ([#189](https://github.com/e7217/anygarden/pull/189))
- Keep `engine_secrets` out of agent
  `/proc/self/environ` (#184 follow-up)
  ([#193](https://github.com/e7217/anygarden/pull/193))

### Fixes

- Symlink host `~/.codex/auth.json` into per-agent
  `CODEX_HOME`
  ([#214](https://github.com/e7217/anygarden/pull/214))
- Redirect `CODEX_HOME` per-agent so MCP templates load
  ([#213](https://github.com/e7217/anygarden/pull/213))
- Serialize per-agent reconcile with lock + pre-reservation
  (#183) ([#191](https://github.com/e7217/anygarden/pull/191))
- Mark manifest stopped after `request_replacement` (#182)
  ([#187](https://github.com/e7217/anygarden/pull/187))

### Refactors

- Remove `codex-extra` virtual engine
  ([#258](https://github.com/e7217/anygarden/pull/258))


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

- Log the resolved anygarden-agent binary on every spawn
  ([#38](https://github.com/e7217/anygarden/pull/38))
  — new ``agent_binary_resolved`` structlog event with
  ``source=(path|uvx)`` and the absolute path (or ``None`` for
  the uvx fallback). Forensic breadcrumb for "which
  anygarden-agent actually ran?" version-skew debugging. No
  change to the discovery priority.

### Earlier (post-0.1.0, no separate release)

- Hide ``max_agents`` from user-facing surfaces
  ([#3](https://github.com/e7217/anygarden/pull/3))
- Per-agent model + reasoning effort selection
  ([#5](https://github.com/e7217/anygarden/pull/5))


## v0.1.0 (2026-04-14)

Initial release — daemon that hosts agent subprocesses, publishes
heartbeats over WebSocket, and reconciles the cluster's declarative
desired-state for spawn / stop / drain operations.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>

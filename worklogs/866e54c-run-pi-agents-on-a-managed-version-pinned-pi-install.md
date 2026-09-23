# feat(machine): run Pi agents on an AnyGarden-managed, version-pinned Pi install

- Commit: `866e54c`
- Author: Changyong Um
- Date: 2026-09-23
- PR: — (issue #688, split from #685)

## Situation

`RoomExecutionAdapter.start()` ran `shutil.which("pi")`, which picks up whatever `pi` the operator had installed globally. The Pi adapter is verified against exactly one release (0.85.1): the JSON event stream, isolation flags, the `models.json` schema, and the `PI_*` env names all depend on it. A routine `npm i -g …@latest` therefore took every Pi agent on the machine offline with `UNSUPPORTED_RUNTIME`, and it happened in practice with 0.87.1. The machine's own *Update engine* button (#553) ran the same `npm install -g <pkg>@latest`.

## Task

- Give Pi agents a machine-owned Pi install at the pinned version, separate from the operator's global install.
- Use `PATH` only as an explicit opt-in, never as a silent fallback (the same concern as #683).
- Make *Update engine* and detection work with the managed install, and document air-gapped pre-seeding.

## Action

- `anygarden_machine/engines/managed.py` defines the pinned version, the prefix `~/.anygarden/engines/pi-cli/<ver>/` (overridable with `ANYGARDEN_MANAGED_ENGINES_DIR`), executable resolution, the `ANYGARDEN_PI_USE_PATH` opt-in, and `ensure_managed_pi()`. That function installs only when a global `pi` exists or `ANYGARDEN_MANAGED_PI=1` is set, skips when `ANYGARDEN_MANAGED_PI=0`, and never raises.
- New `NpmManagedPrefix` channel, the #553 `Channel` extension point: `latest_version` is the pinned version (no network), and `update_argv` runs `npm install --prefix <prefix> --no-audit --no-fund <pkg>@<pinned>`. The `pi-cli` lifecycle now uses `DetectSpec(mode="managed")` with this channel.
- Detector: a new `managed` mode tries the managed executable first, then `PATH` only with the opt-in.
- Daemon: `ensure_managed_pi()` runs before the first register. Spawner: exports `ANYGARDEN_PI_EXECUTABLE` for `pi-cli` agents; an operator-set daemon env value wins.
- Agent: `resolve_engine_executable()` uses the handed-over path, falls back to `PATH` only with the opt-in, and otherwise raises an actionable error. The runtime environment still strips `ANYGARDEN_*`.
- Tests: machine `test_engines_managed.py`, spawner and daemon cases, an autouse fixture isolating the managed root, and agent resolution cases. A sync test asserts that the machine pin equals the agent's `ENGINE_VERSION`.

## Result

- Machine 530, agent 529, and cluster tests pass.
- Real check: `npm install --prefix …/pi-cli/0.85.1 @earendil-works/pi-coding-agent@0.85.1` created `node_modules/.bin/pi`, which reports `0.85.1`, and `detect_engines()` advertised it from the managed path.
- Rollout note: the first daemon start after upgrading runs one npm install (a few seconds) on machines that use Pi. Air-gapped nodes must pre-seed the prefix or set `ANYGARDEN_PI_USE_PATH=1`.
- Follow-up (not done): the same treatment for `codex-cli`.

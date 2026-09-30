# refactor(machine): ship the machine daemon inside the anygarden package (#754)

- Commit: `41e4515` (41e4515492be1778a3d1e103b8b1a002f4337d53)
- Author: Changyong Um
- Date: 2026-09-30T12:15:38+09:00
- PR: —

## Situation

`anygarden` (cluster) listed `anygarden-machine>=0.15.0` as a required dependency of its `server` extra. Five cluster modules imported it directly: `app.py`, `node/execution.py`, `node/ownership.py`, `workspaces/managed_router.py` and `ws/machine_handler.py`. The integrated node runs the daemon in-process. Even so, the two packages had separate versions, PyPI releases, CHANGELOGs, CI steps and release tags, and a version floor (#581) was the only thing keeping them paired. PyPI had `anygarden` 0.18.0 and `anygarden-machine` 0.14.1, while the repo's machine was at an unreleased 0.15.0.

## Task

- Make `anygarden` the single distribution, with the daemon importable as `anygarden.machine`.
- Keep a light daemon-only install (`anygarden[machine]`, no server stack) for remote hosts.
- Point every place that hardcoded the `anygarden-machine` distribution name at the new package: self-update, install script, systemd unit, admin UI commands, the server's update check.
- Constraint: the user is the only user. One remote host runs server, machine and agent and will be reinstalled by hand, so no compatibility shims are needed.

## Action

- Moved `packages/machine/anygarden_machine/` → `packages/cluster/anygarden/machine/` and `packages/machine/tests/` → `packages/cluster/tests/machine/` (with a new `__init__.py`). Rewrote `anygarden_machine` → `anygarden.machine` with a word-boundary substitution across 69 files; `anygarden_machines_online` was left untouched. Moved the machine docs to `packages/cluster/docs/machine/`, moved `install.sh` to `packages/cluster/scripts/install-machine.sh`, and kept the old CHANGELOG as `CHANGELOG-anygarden-machine.md`. Deleted the rest of `packages/machine/`.
- `packages/cluster/pyproject.toml`: the `machine` extra now holds the daemon's dependencies, and `server` and `dev` reference `anygarden[machine]`. Hatchling expands that self-reference into the wheel metadata. Removed the workspace sources in both `pyproject.toml` files and ran `uv lock`.
- Self-update:
  - `install_manifest.PACKAGE_NAME = "anygarden[machine]"`.
  - In `install_detect`, `MACHINE_PACKAGE` follows `PACKAGE_NAME`, and the new `LEGACY_MACHINE_PACKAGE = "anygarden-machine"` is normalized away in `resolve_install`.
  - Without a manifest, a pip install always resolves to the `anygarden` umbrella, so `_has_distribution` was removed.
- `machine/__init__.py` re-exports `anygarden.__version__`, so `daemon_version` reports the `anygarden` version.
- `machine/cli.py`: user-facing messages now say `anygarden machine ...`. The launcher shim keeps the name `~/.local/bin/anygarden-machine` but execs `<venv>/bin/anygarden machine "$@"`. The systemd unit keeps the name `anygarden-machine.service`, and its `ExecStart` is now `-m anygarden.machine.cli run`.
- Server: `api/v1/system.py` `_CHECK_PACKAGES = ["anygarden"]`, plus comment updates in `machines.py`, `models.py` and `ws/manager.py`.
- Frontend: install, run, service and workspace commands in `MachineConnectionDialog.tsx`, `lib/workspaceAttachments.ts` and the `admin.ts` i18n strings. `AdminSystem.tsx` drops its `anygarden-machine` branch.
- CI, release and Makefile: removed the Linux machine pytest step, the `anygarden-machine-v*` tag and mapping, and `release-machine`. The Windows step runs `tests/machine/test_safefs_win.py` and `tests/machine/test_proc_kill.py` from `packages/cluster`. Updated `scripts/test.ps1`.
- Docs: README (two packages; also dropped a stale "TypeScript agent runtime" note), CONTRIBUTING, `packages/cluster/README.md`, the `federation-two-node` and `ANY-2` runbooks, and `packages/agent/docs/local-execution.md`.
- Import-order (I001) fixes in four files whose sort order changed with the rename.

## Decisions

- **Import path.** Weighed three options: keep a top-level `anygarden_machine` inside the `anygarden` wheel; move to `anygarden.machine` with a re-export shim; move with no shim. Chose the move without a shim. A shim has no beneficiary when there are no external users, and the rename was mechanical (313 occurrences). `anygarden/__init__.py` only reads package metadata, so `anygarden.machine` imports on a `[machine]`-only install. This was verified in a clean venv.
- **Transition.** Rejected a final `anygarden-machine` release that would only depend on `anygarden[machine]`. Its sole beneficiary would be one remote host, and reinstalling that host by hand is simpler and more certain.
- **Legacy manifest.** `install.sh` hosts record `package: "anygarden-machine"` in `install.json`. Rather than rely on a manual "delete the manifest" step, `resolve_install` normalizes the old value, which is a few lines of code and pinned by a test.
- **Names kept.** The systemd unit and the launcher shim keep the `anygarden-machine` name. Renaming the unit would add a disable-old/enable-new step on every host. On `install.sh` hosts the venv `bin` is not on PATH, so the shim is the only command users can actually type. The install script's next-step hints therefore use `anygarden-machine`, not `anygarden machine`.
- **No protocol version check.** An integrated node cannot run mismatched versions. Remote daemons report `daemon_version`, and frames ignore unknown fields. Revisit if a breaking wire change lands while remote daemons lag behind.
- **Assumption:** there is only one user and one remote host. If other deployments appear, the missing transition release and the removed `anygarden-machine` script become real breakage.

## Result

- Single distribution: the built wheel contains `anygarden/machine/*`, has no `anygarden-machine` console script, and lists machine dependencies under both the `machine` and `server` extras.
- Clean-venv check (Python 3.11): installing `anygarden[machine]` from the wheel pulls no fastapi, sqlalchemy, uvicorn, alembic, `anygarden_agent` or `anygarden_machine`. Every `anygarden.machine.*` submodule imports, `anygarden machine --help` lists all commands, and the daemon version is 0.19.0.
- Tests:
  - Cluster full suite (now including the machine tests): 2626 passed and 1 failed. The failure was `test_ws_handler.py::TestAgentCausalLink`, which passed on rerun (the class, and the case three times in a row) and is a known flake family.
  - Agent: 680 passed. Frontend vitest: 768 passed, and `npm run build` succeeds.
  - `uv lock --check` passes, and the total ruff violation count equals main (1641).
- New tests: legacy manifest normalization, `anygarden[machine]` update commands, the shim exec'ing `anygarden machine`, and the daemon version matching `anygarden`.
- Pending:
  - Release a new `anygarden` version, then reinstall the remote host (steps in the PR).
  - Update the gitignored local `CLAUDE.md` project overview after merge.
  - Follow-up: merge `anygarden-agent` the same way.

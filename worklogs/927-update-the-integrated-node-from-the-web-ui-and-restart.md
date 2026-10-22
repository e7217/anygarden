# feat(node): update the integrated node from the web UI and restart it in place (#927)

- Commit: `7c42628` (7c4262825b1434c0a73b84596fdd0c16b4ef91ce)
- Author: Changyong Um
- Date: 2026-10-22T19:06:41+09:00
- PR: #927 (issue)

## Situation

The web update button only worked for a standalone machine daemon: it reinstalls and exits, and systemd restarts it (#550). An integrated node (`anygarden start`) runs the API and its local machine in one process, usually in a terminal with no supervisor, so the path was blocked three ways — the API returned 409 for the local machine (`api/v1/machines.py:393`), the local bus dropped `self_update` (`node/execution.py:182`), and machine links set `allow_self_update=False`. The "This server" entry showed "update available" with no way to act on it, and the local machine's Update button only produced a 409 toast.

## Task

- Let an admin update an integrated node from the web, whether it runs in a foreground terminal or under systemd.
- A failed install must leave the running version serving; never install a version the migrated DB would refuse (#920).
- Refuse clearly (with a reason) where in-place update cannot work: `anygarden server`, non-POSIX, source checkout, unknown install method.
- Stop offering the always-failing per-machine update on the node's own machine.

## Action

- `packages/cluster/anygarden/node/self_update.py` (new): `build_node_update_command` pins `anygarden[server,agent]==X` (`uv tool install … --force` or `<python> -m pip install --upgrade …`, reusing `install_detect.resolve_install`); `unsupported_reason` (not integrated / non-POSIX / editable or local-dir install via `direct_url.json` / unresolvable install); `reexec_command` → `[sys.executable, -m, anygarden, *argv[1:]]`; `NodeUpdater` state machine `idle → installing → restarting` or `failed`, refusing concurrent runs (`NodeUpdateBusy`) and non-newer/invalid versions (`NodeUpdateRejected`), installer run via `asyncio.to_thread` with the daemon's 300 s timeout.
- `packages/cluster/anygarden/api/v1/system.py`: admin-only `GET /api/v1/system/node-update` (supported, reason, status, versions, error) and `POST` (202; default target = cached latest from the update check). The restart callback sets `app.state.node_restart_requested` and calls the existing `node_shutdown_callback`.
- `packages/cluster/anygarden/cli.py`: `start_node` snapshots `launch_env` before `_apply_runtime_env`; after the existing clean-shutdown checks it calls `_reexec_node`, which flushes output and `os.execve`s the same arguments (OSError → `_NodeExitError` with recovery hint).
- `packages/cluster/anygarden/__main__.py`: `python -m anygarden` now calls `dispatch` (the console-script entry) instead of the legacy server `main`; the re-exec found this via the real-CLI test ("No such command 'start'").
- `api/v1/errors.py`: registered `NODE_UPDATE_UNAVAILABLE|NO_TARGET|REJECTED|BUSY` (409); `docs/api.md` registry table and `tests/test_api_errors.py` snapshot updated.
- Frontend: `useNodeUpdate` + pure `nextNodeUpdateProgress` in `hooks/useSystemVersion.ts` (2 s polling while busy; failed polls during restart = restarting; back on a different version = updated, same version = same_version, >3 min unreachable since last answer = stalled). `ServerVersionPanel` shows "Update to X and restart" with a confirm dialog and an `aria-live` status, or the manual command plus the unsupported reason; after success only "Reload" remains. `AdminMachines` replaces the local machine's Update button (identified by the server-enforced `anygarden.local_node_id` label) with "Update with this server". en/ko catalog strings.
- Docs: `docs/requirements.md` NODE-17 (pytest) / NODE-18 (vitest); `docs/runbook/local-node.md` "Updating from the web UI"; CHANGELOG.
- Tests: `tests/test_node_self_update.py` (command, support reasons, re-exec argv, state machine), `tests/test_system_api.py` (admin-only, unsupported 409, cached target, downgrade refusal), `tests/test_local_node.py::test_real_cli_web_update_restarts_the_node_in_the_same_process` (real subprocess, stubbed installer: same PID healthy again, identity kept, owner `running`, update state idle), vitest for the progress reducer/panel/local machine, Playwright `e2e/admin-node-update.spec.ts` (install → connection refused → back on 0.22.0).

## Decisions

- **Re-exec instead of relying on systemd** (issue revised during planning): systemd-only would leave the common foreground `anygarden start` without web update. `os.execve` after the normal shutdown keeps PID and terminal and also works under systemd (no restart seen). Rejected: spawning a child and exiting (detaches from the terminal, breaks `stop`'s ownership model).
- **Install before shutdown**: a failed install costs nothing; only a successful install takes the node down.
- **Always pin the version** (cached latest by default): `uv tool upgrade` cannot pin and keeps whatever constraint the original install had; pinning makes the result deterministic and lets the UI check the returned version.
- **Newer-only**: an older build refuses a DB already migrated past its head (#920), so a downgrade could leave the node unable to start.
- **Launch-time environment**: `_apply_runtime_env` writes `ANYGARDEN_*` variables; re-exec uses the snapshot so config-file precedence is unchanged.
- **Local machine detection by label** rather than a new `is_local` API field: the label is enforced server-side (`PATCH` cannot change it); avoids touching every `MachineOut` endpoint.
- Out of scope: automatic rollback, unattended updates, Windows (`execve` does not replace the process there), per-link updates (links live in the node process).
- Revisit if: the node ever runs multiple workers or `--reload` (currently refused), or extras beyond `server,agent` become required.

## Result

- Admins can update a released POSIX node from **Admin → Machines → This server**; the page follows the restart and reports the new version.
- Verified: cluster pytest 4068 passed / 7 skipped; vitest 1065 passed; Playwright 83 passed (CI mode); `npm run build` OK.
- Not verified live against a real PyPI release (installer stubbed in tests); a real end-to-end upgrade happens with the next release.

# feat(machine): serve another server as a machine while keeping the local node (#848)

- Commits: work-in-progress commits on `feat/848-machine-links` (squashed in the PR)
- Author: Changyong Um (with Claude)
- Date: 2026-10-15
- Issue: #848

## Situation

Agents on another *node* (federation) can only be reached through shared
channels: they can't be DM'd or added to ordinary rooms. Agents on another
*machine* of the same server behave like local agents. Both of the user's
computers ran `anygarden start`, so neither could be a machine of the other.

Running `anygarden machine run` next to a node was unsafe. Both daemons
defaulted to `~/.anygarden/agents` and `~/.anygarden/workspace-signing.key`.
At start, a daemon re-adopts every live process recorded in that directory,
then stops any agent its server's full snapshot does not list. So a second
daemon could take over and stop the node's agents. Connecting a machine also
required a terminal (`connect`, `run`, systemd).

## Task

- Let a computer keep its node and also serve another server as a machine.
- Make two daemons sharing one agents directory impossible, in either start
  order, without breaking a node that was stopped and turned into a plain
  machine.
- On a node, set this up from the web UI only.
- Keep existing remote machines (default layout, re-adopt across upgrades)
  unchanged.
- Keep the connection direction: the machine dials the server, and the
  server sends instructions over that socket.

## Action

- **Paths:** `machine/config.py` `MachinePaths.for_data_dir()` keeps
  config, token, `agents/`, workspace registry and signing key under one
  directory. `None` keeps the historical layout. `save_connection` takes
  the paths.
- **Lock:** `machine/agent_root_lock.py` `AgentRootLock` takes an exclusive
  `flock`/`msvcrt` lock on `<agents_root>/.daemon.lock`. Three daemons hold
  it:
  - `machine run`
  - the node's built-in machine (`node/execution.py` start, released only
    after confirmed cleanup)
  - each node-managed link
- **CLI** (`machine/cli.py`):
  - `--data-dir` / `ANYGARDEN_MACHINE_DATA_DIR` on `connect`, `run`,
    `status`, `workspace`, `install-systemd-unit` and `update`.
  - `run` passes every path, including `token_path`. Token rotation used to
    write to the default file.
  - systemd gets one unit per directory (`anygarden-machine-<hash>`, quoted
    `ExecStart --data-dir`).
  - Server URL and token validation moved to `machine/link_code.py`.
- **Link code:** `machine/link_code.py` encodes and decodes `agmachine1.`
  (base64url JSON: server, machine ID, token, name), with the same
  validation as `connect`.
- **Daemon** (`machine/daemon.py`):
  - `allow_self_update=False` answers `self_update` with a failure instead of
    reinstalling and exiting.
  - Read-only `connection_state` and `last_error`. A handshake 401 or 403 is
    reported as `auth_failed`.
- **Link manager:** `node/machine_links.py` `MachineLinkManager` saves links
  under `<data-dir>/remote-machines/<machine-id>/` and runs a `MachineDaemon`
  task per link inside the node process.
  - It resumes links when the node starts and stops them (draining their
    agents) when it closes.
  - It refuses this node's own machine and duplicate links.
  - It reports a directory another daemon holds as an error and refuses to
    delete it.
- **Admin API:** `api/v1/machine_links.py` serves
  `GET/POST /api/v1/machine-links` and `DELETE /{id}`. It is admin only and
  returns 404 on a non-integrated server. The body is parsed by hand so the
  token is never echoed. `app.py` wires it into the node lifespan, closing
  links before local execution.
- **Frontend:**
  - `MachineConnectionDialog` shows the connection code while the one-time
    token is visible.
  - New `MachineLinksSection` (+ `useMachineLinks`) in the Machines rail
    lists, adds and disconnects links, with text statuses and hints. It is
    hidden on a plain server.
  - i18n ko/en.
- **Docs:**
  - `docs/requirements.md` adds NODE-11/12/13.
  - `docs/runbook/local-node.md` gains a section on serving another server.
  - `DESIGN.md` §9 and `CHANGELOG.md` updated.

## Decisions

- **`--data-dir` with an unchanged default**, over auto profiles or a new
  default path. A new default would leave upgraded remote machines unable to
  re-adopt agents already running in `~/.anygarden/agents` (#451 regression).
- **Lock on the agents directory**, over refusing node data directories.
  Refusing node directories would block a node that was stopped and turned
  into a plain machine. It also would not stop a node started *after* a
  daemon. The real hazard is two concurrent owners of one `agents/`.
- **In-process daemon task for web UI links**, over a child process or a
  systemd unit. The built-in machine already runs a `MachineDaemon` subclass
  in-process. A child process needs supervision and can leave orphans. A
  systemd unit would let a web request manipulate user services. Revisit if a
  daemon fault ever disturbs the node's event loop.
- **The code is built in the X frontend**, over a new X endpoint. The token is
  already shown in plain text there, so exposure does not grow and X's
  backend is untouched.
- **A link refuses remote self-update.** Otherwise X could reinstall the
  node's package and stop its process.
- **Known gap, out of scope:** managed Pi installs have no cross-daemon lock.
  Two daemons installing the same version at once could race.

## Result

- Tests:
  - New: lock, paths, CLI data-dir/systemd/update, link code, daemon
    self-update refusal and connection state, link manager lifecycle, admin
    API, node refusing a held directory.
  - `uv run pytest -n logical` (cluster) passes.
  - Frontend: `npm run build` passes and 121 vitest files (908 tests) pass.
- Live, two `anygarden start` nodes (X :18101, Y :18102, separate data dirs):
  - Y's API accepted X's code, and the link reached `connected`. X listed the
    machine online, and Y's own machine stayed online.
  - Screenshot: Y's Machines rail shows "다른 서버에 제공" with the link
    "연결됨".
  - After restarting Y, the link resumed and reconnected.
  - Removing the link made X show the machine offline and deleted the
    directory.
- **Not verified live:** an agent spawned through the link. No engine was
  detected on the test VM. This path is the unchanged `MachineDaemon`
  reconcile, which the existing remote-daemon tests cover.

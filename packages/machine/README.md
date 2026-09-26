# drmachine

Machine daemon for Anygarden agent orchestration. Connects to the drhub server via WebSocket and manages agent subprocesses on the local machine.

## Installation

```bash
pip install -e ".[dev]"
```

## Usage

```bash
# Register this machine with a Anygarden server
anygarden-machine register --server wss://anygarden.example.com --name my-machine

# Run the daemon
anygarden-machine run

# Check status
anygarden-machine status

# Install as systemd user service
anygarden-machine install-systemd-unit
```

## Connect a machine created in the web app

Deploy the cluster UI/API and the machine package from the same release. The
web connection guide requires a machine package that includes `connect` (check
`anygarden-machine connect --help`). Older PyPI installations may not include
this command until that release is published; upgrading only the UI is not a
complete remote-machine rollout. For development, install this checkout with
`python -m pip install -e packages/machine` from the repository root.

If the administrator has already created a machine in the web app, connect that
existing identity instead of registering another machine:

```bash
anygarden-machine connect --server https://garden.example.com --machine-id MACHINE_ID
# Paste the web-issued machine token at the hidden prompt.
anygarden-machine run
```

`connect` saves `~/.anygarden/machine.toml` and `~/.anygarden/machine.token` with
private file permissions. It never sends the token as a command-line argument or
prints it. The command does not contact the server or create a second machine.
`run` uses those saved settings after a restart; use the web guide's connection
check to verify authentication and reachability. One machine identity is saved
per operating-system account. Replacing a different saved identity requires an
explicit `connect --replace`; stop its daemon/service first.

If installed in a virtual environment, activate that environment again in a new
terminal before running `anygarden-machine run`. For optional Linux startup,
stop the foreground daemon before enabling its service:

```bash
anygarden-machine install-systemd-unit
systemctl --user daemon-reload
systemctl --user enable --now anygarden-machine
```

## Workspace registry selection

The released implementation currently does **not** advertise external workspace
root enforcement or audit support, for either the remote daemon or integrated
node. Registering a folder does not enable access. The UI reports this limit and
continues to show existing approvals and revocation; it offers new requests only
when the server confirms support. The server supports only restricted Codex read
access with a compatible implementation, and rejects external writes.

For implementations that support the required execution controls, register a
canonical Git repository root with at least one commit and no symlink traversal.
The machine-local registry stores paths; the server receives opaque IDs and
labels. Registration and consent must use the **same** registry as the daemon:

```bash
# Remote daemon: ~/.anygarden/workspaces.json
anygarden-machine workspace register /path/to/repository --label project --max-mode read --allow src
anygarden-machine workspace list

# Integrated node: use the exact --data-dir of the running `anygarden start`.
anygarden-machine workspace --node-data-dir /path/to/node-data register /path/to/repository --label project --max-mode read --allow src
anygarden-machine workspace --node-data-dir /path/to/node-data list
```

The integrated commands use `/path/to/node-data/workspace-registry.json`.
Consent commands also require that same `--node-data-dir`; the admin UI uses the
running node's actual directory. Restart the corresponding daemon/node after
registration so its catalog is published. Room approval, system approval, and
short-lived machine-local consent remain separate checks.

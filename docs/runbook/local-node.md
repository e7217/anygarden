# Integrated local node

`anygarden start` runs the web/API and one local execution manager together in
the foreground. `anygarden stop` asks that same data-directory owner to shut down
and confirms that its process cleanup and ownership release finished. The
machine daemon does not open a local WebSocket. Agent room WebSockets remain in
this phase; removing that separate agent relay belongs to issue #589.

## Install and run

From a checkout containing this change:

```sh
uv sync --frozen --all-packages --all-extras
uv run --frozen --package anygarden --all-extras anygarden start --data-dir /path/to/node --port 8000
# In another terminal, under the same operating-system user:
uv run --frozen --package anygarden --all-extras anygarden stop --data-dir /path/to/node
```

A packaged installation needs the server and agent extras in the **same Python
environment**: `pip install 'anygarden[server,agent]'`. The machine dependency is
already included by the server extra. These new commands require a version that
contains this change; this PR does not publish a package.

The default data directory is `~/.anygarden`. A fresh explicit data directory
contains its own DB, JWT/MCP keys, node identity, agent directories, workspace
receipt key, and uploaded files. `--config` selects a config file; otherwise the
node reads `data-dir/config.env`. Explicit `ANYGARDEN_*` settings retain their
existing precedence. Check the configured database and file directories before
using an existing configuration. No peer connection is needed for local rooms
and tasks.

The first normal user signup is still the administrator. The local Machine row
is automatically bound to that account (or an existing administrator); no extra
machine registration or machine token is needed. No synthetic admin account is
created. Detected engines become available through the usual machine inventory.
Engine detection reports installation, not a successful provider invocation.

Integrated mode supports **one API worker**. `--workers` other than 1,
`WEB_CONCURRENCY` other than 1, and `--reload` fail explicitly. Use stop/start for
restarts. Existing `anygarden server`, `machine`, `agent`, `client` and the legacy
server alias retain their behavior. Remote machine WebSockets remain available
for other Machine IDs; the internal local ID refuses network ownership, token
regeneration, deletion, and daemon self-update; it is updated together with the
node (see *Updating from the web UI*).

## Ownership and delivery

`node-identity.json` stores independent `node_id` and internal `machine_id` UUIDs.
Restore that file with the node DB and other state. Changing hostnames or URLs
must not create a new identity. Do not run copies of the same identity as peers.

An OS file lock on `node.lock` is held across startup, execution, shutdown, and
resource cleanup. Both local execution and server resource cleanup must succeed
before the stopped receipt is written. Foreground `start` and the requesting
`stop` command exit nonzero if cleanup fails. The lock file is never replaced or removed by the program.
`node-owner.json` is atomically written and flushed before execution is allowed;
its state is `starting`, `running`, or `stopped`. A missing/corrupt/unclean receipt
is not proof that an earlier child exited. A second owner cannot overwrite it.

Stop uses an owner-nonce request in `node-stop.json`, not an unauthenticated HTTP
endpoint or a PID signal. A stale request cannot stop a subsequent owner. Stop
waits for the OS lock and the matching confirmed shutdown receipt; timeout does
not force-kill anything or claim success. Permissions assume the data directory
is controlled by the operating-system account running the node, as with the
existing local machine storage. The agent runtime is not a separate OS sandbox.

Local delivery uses the same MachineBus-shaped interface as remote transport:
`send(machine_id, frame) -> bool`, `is_connected`, `connected_ids`. Acceptance
only means queued delivery. Local register/report/token/replacement frames use
the existing lifecycle handlers and conditional DB updates. Generation, dispatch
lease, stopped high-watermark and room authorization remain authoritative.
No Task or Machine schema change is introduced.

The integrated backend passes an explicit OS bootstrap environment to agent
processes (executable lookup, home/temp directories, Windows process variables,
and locale). It does not inherit server/application configuration, ambient
provider credentials, Python import hooks or arbitrary future environment keys.
The spawner then adds only the agent-scoped token, configured MCP token and
manifest execution settings; explicitly assigned engine credentials keep their
existing stdin transport. The standalone daemon retains its legacy inheritance.
This environment boundary is not an OS/filesystem sandbox.

Shutdown refuses new spawns, discards unapplied transport messages, waits for
already-started process creation, terminates owned process trees, marks the local
machine offline and releases ownership last. Durable server desired state is
reconciled on the next clean start. This does not claim exactly-once external tool
effects or certify the new runtime/session protocol in #589.

## Existing installation and rollback

1. Gracefully stop the old server and local daemon; confirm their agent process
   trees have stopped. Back up the DB and the complete data directory together.
2. Inspect the configured paths and existing migration revision. On start, an
   older SQLite revision is first snapshotted with SQLite's backup API to
   `<data-dir>/backups/anygarden.db.bak-<old-revision>-<UTC>` (the newest three
   are kept), then upgraded to head; the `startup.schema_backup` warning names
   the file. If the snapshot cannot be written, start refuses and leaves the DB
   unchanged. A revision this build does not know (written by a newer
   anygarden), several `alembic_version` rows, or an older non-SQLite DB are
   refused: back those up with the database's own tools and run
   `anygarden server migrate`. Fresh empty DBs are initialized (#920).
3. Start the integrated node. It creates a distinct internal local Machine row;
   it does not seize an old daemon's machine token/ID. Existing stopped agents
   can be explicitly started/placed through normal controls. Existing remote
   machines and their rows are preserved.
4. To roll back, stop the integrated node and confirm cleanup first. Use the old
   server/daemon commands and their original configuration/tokens. Reassign agents
   from the now-offline internal Machine through the existing placement controls.
   Never overlap old and new local execution owners. To undo a schema upgrade
   made by start, stop the node, copy the matching `backups/` file over the DB
   and run the previous anygarden version.

## Updating from the web UI (#927)

An admin can update a node started with `anygarden start` from **Admin >
Machines > This server**. After *Check for updates* finds a newer release, the
**Update to X and restart** button:

1. installs `anygarden[server,agent]==X` while the node keeps serving — with
   `uv tool install … --force` for a `uv tool` install, otherwise
   `<python> -m pip install --upgrade …` in the node's environment;
2. on success, shuts down exactly like `anygarden stop` (agents stop their
   current work, ownership is released);
3. re-executes itself as `<python> -m anygarden <same arguments>` with the
   environment it was launched with — same PID, same terminal, no supervisor
   needed. A systemd unit sees no restart.

The new build then backs up and upgrades the database (#920). The page follows
the node through the restart and reports the version it came back on; reload
the page to load the new interface.

- **Install fails**: nothing is shut down; the node keeps running the current
  version and the panel shows the installer's error.
- **New version does not start** (import error, migration failure): the
  terminal shows the error and the process exits. Reinstall the previous version
  (`uv tool install "anygarden[server,agent]==<old>" --force` or the pip
  equivalent), and if the database was already upgraded restore the matching
  `backups/` file as described above, then `anygarden start`.
- Only a version newer than the running one is installed: an older build would
  refuse a database already migrated past its head.
- Not available (the panel shows the command and the reason instead) for
  `anygarden server` without an integrated node, non-POSIX systems (`os.execve`
  cannot replace the process), a source checkout (editable install), or an
  environment that is neither a `uv tool` install nor has pip.
- The node's own machine and machine links (#848) run inside the node process
  and are updated with it; their per-machine update button is not used.

## Unconfirmed shutdown

After SIGKILL, a crash, malformed ownership evidence or unconfirmed child cleanup,
startup fails with **Node recovery required** before starting any agents. This is
intentional: automatically starting again could repeat work whose side effects
are unknown. A leftover `agents/*/runtime.json` also prevents new execution even
when an ownership record claims a clean stop.

Recovery requires an operator to inspect the old PID/process-tree information,
verify process identity (not merely reuse the recorded PID), stop remaining owned
processes, and reconcile any in-flight work and side effects. Preserve the old
owner/runtime evidence for diagnosis. Only after execution is confirmed stopped
may the operator archive the obsolete runtime receipts and repair the matching
`node-owner.json` state to `stopped`; preserve `node-identity.json`. If evidence is
insufficient, keep the node stopped. Do not remove `node.lock` to bypass a live
owner. There is no automatic retry or force-recovery flag.

## Validation scope

`packages/cluster/tests/test_local_node.py` covers independent-process ownership,
unclean exit refusal, nonce-scoped stop, first-signup provisioning, network-owner
rejection, local lifecycle generation/stop fencing, partial startup/cleanup
failures, shutdown during a real subprocess creation, and real CLI start/stop/
restart on a temporary local database. It also checks rejection of implicit
schema upgrades. Engine interaction in these tests is fake/provider-free.
Existing cluster and machine suites cover preserved legacy paths. Two-node
federation, TLS, remote delegation and live provider success remain separate
issues, not claimed by this local-node change.

## Federation (peer) listener — opt-in

Starting the node with `--peer-port N [--peer-host H]` additionally runs the
mTLS federation listener (default bind `0.0.0.0` when the port is given).
Without the option no listener runs — the default boot is unchanged.

Requirements and behavior (PR609/PR615 fail-closed contract):

- Peer credentials live under `<data-dir>/peer/` (layout below). On the first `--peer-port` start, when that default
  directory holds neither file, the node creates the pair (reusing the node ID
  from `node-identity.json` when present) and prints the node ID and SHA-256
  fingerprint; later starts only load and print them (#839). It never
  overwrites or rotates a pair — rotation stays explicit with
  `anygarden federation init-credentials --force`.
- Startup is refused before any boot work when the pair is partial, when it
  is unusable (corrupt, not yet valid, mismatched), or when
  `ANYGARDEN_PEER_CREDENTIALS_DIR` is set but holds no pair (most likely an
  unmounted volume; a fresh identity there would silently break peers).
  `anygarden federation init-credentials` still creates a pair ahead of time.
- New nodes get a root layout (#843): `peer-root-cert.pem`/`peer-root-key.pem`
  (pinned by peers, 10 years) and `peer-leaf.pem` (TLS, 90 days). `start`, app
  startup and a six-hourly task renew the leaf within 30 days of expiry, and
  the listener serves it without a restart. Legacy `peer-cert.pem` pairs still
  load; see the two-node runbook for switching.
- An expired but intact pin does not stop the node (#842): `start` prints a
  warning, runs without the peer listener, and the node APIs answer
  `CERTIFICATE_EXPIRED` (503). It also warns 30 days before expiry.
- `--peer-public-url https://host:port` (or `ANYGARDEN_PEER_PUBLIC_URL`) is the
  address peers dial. It only prefills connection cards and invite codes.
- The listener starts only after the app lifespan composed the peer/channel
  services, and stops before the local execution backend closes — both
  `anygarden stop` and SIGINT/SIGTERM close the port and exit 0 after
  confirmed cleanup (PR605 exit-code contract).
- `task.execute` actions from remote peers additionally pass a local policy
  gate (PR615): the current inbound grant must be active, unexpired, carry
  `task.execute`, and list the authenticated remote agent among its actors.
  The gate is deny-by-default and only ever adds rejections.

## Serving another server from a computer that runs a node (#848)

A computer that runs `anygarden start` can also be a machine of another
AnyGarden server, without stopping its own node. The other server places
agents on it; this node keeps its own agents.

Web UI (no terminal on this computer):

1. On the other server, open Admin > Machines, register a machine, and copy
   the **connection code** (`agmachine1.…`) from the connection guide. It
   carries the machine token: treat it like a password.
2. On this node, open Admin > Machines > **Serve another server** > Add, and
   paste the code.

The node saves the connection under `<data-dir>/remote-machines/<machine-id>/`
and runs a machine daemon for it inside the node process. It dials the other
server (this computer must reach that server's address; the server never
dials back), starts and stops with the node, and stops its agents when the
connection is removed. Such a connection refuses the other server's
self-update request: its version follows this node.

Terminal (computers without a node, or headless servers) — give the daemon
its own directory:

```bash
anygarden machine connect --data-dir ~/.anygarden/remote-machines/<machine-id> \
  --server 'http://<server>:8000' --machine-id '<machine-id>'
anygarden machine run --data-dir ~/.anygarden/remote-machines/<machine-id>
# or: anygarden machine install-systemd-unit --data-dir … (one unit per directory)
```

Every daemon — the node's built-in machine, `anygarden machine run`, and a
node-managed connection — holds an exclusive lock on its `agents/` directory.
A second daemon pointed at the same directory refuses to start instead of
re-adopting and then stopping the first one's agents. `--data-dir` (or
`ANYGARDEN_MACHINE_DATA_DIR`) also applies to `status`, `workspace` and
`update --restart`. Without it the historical `~/.anygarden` layout is used.

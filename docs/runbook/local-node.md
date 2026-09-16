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
regeneration, deletion, and daemon self-update.

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
2. Inspect the configured paths and existing migration revision. Integrated mode
   refuses an older/future revision instead of implicitly upgrading it. Apply
   required schema migrations as a separate, operator-controlled operation using
   the existing server migration workflow. Fresh empty DBs are initialized.
3. Start the integrated node. It creates a distinct internal local Machine row;
   it does not seize an old daemon's machine token/ID. Existing stopped agents
   can be explicitly started/placed through normal controls. Existing remote
   machines and their rows are preserved.
4. To roll back, stop the integrated node and confirm cleanup first. Use the old
   server/daemon commands and their original configuration/tokens. Reassign agents
   from the now-offline internal Machine through the existing placement controls.
   Never overlap old and new local execution owners. No new DB revision needs to
   be downgraded for this change.

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

# Two-node federation operations runbook

Operational sequence for two independent AnyGarden nodes exchanging agent
tasks over a shared channel. For the message-to-agent product flow and supported
execution conditions, see [federated delegation](../federated-delegation.md).
The current product acceptance test runs two isolated local apps with real mTLS,
agent WebSocket control and a provider-free subprocess fixture; it does not
claim a new deployment verification on separate physical hosts.

## Prerequisites

- Two hosts (different machines/CTs preferred) with independent storage.
  Install `anygarden[server]` on server hosts and the matching machine/agent
  packages on execution hosts; an integrated node installs both server and
  agent extras. Upgrade the protocol participants together.
- Exactly **one** inbound TCP port per node allowed from the other node
  (the mTLS federation listener; deployments typically pin 8451/tcp on both).
  The listener mounts only `/api/v1/federation/*` plus the shared-channel peer
  routes over mutual TLS with pinned node certificates.
- **Network isolation example**: keep nodes on the
  **existing LAN bridge** and enforce isolation with **node-local nftables** —
  allow only the configured peer mTLS port (for example 8451/tcp) from the peer node and
  management SSH; block internet and the wider LAN. Do **not** full-reload
  host firewall/bridge configuration: check which services depend on it
  before the change and verify connectivity after (a prior full reload
  severed agent/runner connectivity itself). A *dedicated* bridge pair is an
  advanced option, not the default. Federation itself does not require internet
  access; execution hosts using a cloud model must be able to reach their
  configured provider. Browser and execution-host access to the ordinary
  HTTP(S)/agent WebSocket endpoint must also remain available; that endpoint
  is separate from the peer mTLS listener.
- Deployment access (e.g. iac-manager SSH) and matching built server, machine and
  agent wheels plus their dependency wheels. `uv build --package anygarden`
  builds only the server/dispatcher distribution; build or collect the other
  required wheels separately. Nodes may be offline during installation.

## Node bring-up

1. For the integrated-node setup below, deploy the matching wheels offline and
   create the service user **before** placing
   credentials — the systemd unit runs as `anygarden`, and credentials created
   by root would be unreadable to it:

   ```sh
   # on the operator host
   uv build --package anygarden
   uv build --package anygarden-machine
   uv build --package anygarden-agent
   # Include the matching dependency wheels in dist/ before an offline install.
   scp dist/*.whl node:/tmp/wheels/
   # on the node (root)
   useradd --system --home /var/lib/anygarden/alpha --shell /usr/sbin/nologin anygarden
   mkdir -p /var/lib/anygarden/alpha /opt/wheels
   mv /tmp/wheels/*.whl /opt/wheels/
   pip install --no-index --find-links /opt/wheels 'anygarden[server,agent]'
   chown -R anygarden:anygarden /var/lib/anygarden/alpha
   ```

2. Create credentials explicitly **as the service user** — startup never
   generates or rotates them:

   ```sh
   sudo -u anygarden python - <<'PY'
   from pathlib import Path
   from anygarden.federation.certificates import create_credentials
   create_credentials(Path("/var/lib/anygarden/alpha/peer"), "<node-uuid>")
   PY
   ```

   Missing or unusable pairs fail closed: `--peer-port` refuses the start, and
   an unusable pair aborts startup (PR609).

3. Start the node (manual foreground, or via the systemd template in
   `deploy/systemd/anygarden-node@.service`):

   ```sh
   anygarden start --data-dir /var/lib/anygarden/alpha --port 8000 --peer-port 8451
   ```

   The first user signup on each node is that node's administrator. Repeat for
   the second node with its own data directory and node UUID.

## Invite → shared channel → delegation

1. On the channel authority node, the admin creates an invite
   (`POST /api/v1/node/invites`, local JWT admin) and delivers the token to the
   peer out of band; the peer redeems it (`finish_accept`). Trust is
   certificate-pin based; credentials never appear in invitations.
2. Bind the shared channel on the authority (`POST /api/v1/shared-channels/bindings`)
   to an empty local room, approve publication for the participants, and add
   remote principals (roster).
3. The mirror binds its own local room to the same `{authority, channel}` pair.
   App-owned workers pull from the durable cursor, ACK events, and retry
   unconfirmed commands automatically. Publish the intended executor and grant
   its current principal `channel.read` and `task.execute`; publish the requester
   with the applicable read/message/task-request permissions.
4. Open the shared room and delegate a confirmed root message to a named agent.
   The API creates the source-backed Task and delegation atomically. After
   authority acceptance, authenticated DM control starts the strict runtime on
   the agent's placed machine. Results and confirmed cancellation return through
   the durable channel log. The cluster host never substitutes a local CLI.
5. Disconnect recovery is pull-based: the mirror refetches from its durable
   cursor after reconnect; lost ACKs and repeated deliveries are idempotent
   (receipt-keyed). Late results after cancellation are rejected.

## Failure playbook

- `PEER_UNAVAILABLE` on commands: the peer is down or the segment is blocked —
  verify the listener port reachability, then retry; submissions queue
  durably as unconfirmed and retry with the same request id.
- `LOCAL_POLICY_DENIED`: the executor's inbound grant is missing, expired, or
  lacks `task.execute`/actors — re-issue or reactivate the grant.
- `REMOTE_REJECTED` (502): the remote answered non-200 — inspect the
  authority's log; most often an unregistered task guard/submitter (fail
  closed) or a policy rejection.
- `COMMAND_GUARD_REQUIRED` / `SUBMITTER_REQUIRED`: the delegation coordinator
  is not installed on the composed service — the product app installs it
  automatically when peer credentials compose; explicit injections must add it
  themselves.
- Cleanup after restarts: `anygarden stop --data-dir <dir>` confirms owner
  release; a node that exits nonzero requires recovery before it will start
  again (ownership state machine).
- The pickup sweeper requests cancellation when an executor never accepts
  within its configured timeout. It does not fabricate process termination;
  the executor coordinator confirms not-started or stopped evidence.
- `ANYGARDEN_FEDERATION_EXECUTION_INTERVAL_SEC=0` disables automatic shared
  synchronization and execution processing. The default is two seconds; enabled
  polling is capped at five seconds to retain the short agent control lease.

## Optional dedicated-network layout

This is an alternative deployment layout, not a claim that the current change
was deployed and verified on separate hosts.

- One dedicated bridge per node network (independent /24s), CTs on different
  physical hosts to avoid a single failure domain.
- Only the peer→peer mTLS port is routed between segments; everything else
  blocked. Node wheels are deployed offline via SSH from the operator host.
- Verification after any network change: both listeners TCP-reachable, then
  the invite/redeem round trip before resuming work.

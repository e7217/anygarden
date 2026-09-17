# Two-node federation operations runbook

Operational sequence for two independent AnyGarden nodes exchanging agent
tasks over a shared channel (issue #586 milestone; verified on real machines
in task #50/#54, provider-free).

## Prerequisites

- Two hosts (different machines/CTs preferred) with independent storage; the
  wheel installed on each with both extras: `pip install 'anygarden[server,agent]'`.
- Exactly **one** inbound TCP port per node allowed from the other node
  (the mTLS federation listener; deployments typically pin 8451/tcp on both).
  The listener mounts only `/api/v1/federation/*` plus the shared-channel peer
  routes over mutual TLS with pinned node certificates.
- **Network isolation discipline**: create *dedicated* bridges/segments for
  the node path; never modify existing bridges — past changes severed the
  runner connectivity itself. Internet access is not required.
- Deployment access (e.g. iac-manager SSH) and a built wheel
  (`uv build --package anygarden`); nodes may be offline during deployment.

## Node bring-up

1. Create the data directory and place credentials explicitly — startup never
   generates or rotates them:

   ```sh
   python - <<'PY'
   from pathlib import Path
   from anygarden.federation.certificates import create_credentials
   create_credentials(Path("/var/lib/anygarden/alpha/peer"), "<node-uuid>")
   PY
   ```

   Missing or unusable pairs fail closed: `--peer-port` refuses the start, and
   an unusable pair aborts startup (PR609).

2. Start the node (manual foreground, or via the systemd template in
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
3. The mirror binds its own local room to the same
   `{authority, channel}` pair, pulls the replay from the durable cursor, and
   ACKs. From here `task.request` commands from the mirror run the delegation
   loop: request → accept → started → result/cancel, with the remote executor
   gated by the authority's local policy (current grant + `task.execute`).
4. Disconnect recovery is pull-based: the mirror refetches from its durable
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
- Orphaned delegations (executor never picked up within the pickup timeout)
  are finished by the delegation sweeper (follow-up backlog).

## Homelab isolation pattern (field-tested)

- One dedicated bridge per node network (independent /24s), CTs on different
  physical hosts to avoid a single failure domain.
- Only the peer→peer mTLS port is routed between segments; everything else
  blocked. Node wheels are deployed offline via SSH from the operator host.
- Verification after any network change: both listeners TCP-reachable, then
  the invite/redeem round trip before resuming work.

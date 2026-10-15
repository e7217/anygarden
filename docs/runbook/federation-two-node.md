# Two-node federation operations runbook

Operational sequence for two independent AnyGarden nodes exchanging agent
tasks over a shared channel. For the message-to-agent product flow and supported
execution conditions, see [federated delegation](../federated-delegation.md).
The current product acceptance test runs two isolated local apps with real mTLS,
agent WebSocket control and a provider-free subprocess fixture; it does not
claim a new deployment verification on separate physical hosts.

## Prerequisites

- Two hosts (different machines/CTs preferred) with independent storage.
  Install `anygarden[server]` on server hosts and `anygarden[machine]` (which
  carries the agent runtime) on execution hosts; an integrated node installs
  both server and agent extras. Upgrade the protocol participants together.
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
   # Include the matching dependency wheels in dist/ before an offline install.
   scp dist/*.whl node:/tmp/wheels/
   # on the node (root)
   useradd --system --home /var/lib/anygarden/alpha --shell /usr/sbin/nologin anygarden
   mkdir -p /var/lib/anygarden/alpha /opt/wheels
   mv /tmp/wheels/*.whl /opt/wheels/
   pip install --no-index --find-links /opt/wheels 'anygarden[server,agent]'
   chown -R anygarden:anygarden /var/lib/anygarden/alpha
   ```

2. Start the node as the service user (manual foreground, or via the systemd
   template in `deploy/systemd/anygarden-node@.service`). On the first start
   with `--peer-port` the node creates its peer credentials in
   `<data-dir>/peer` (key mode 0600) and prints its node ID and SHA-256
   fingerprint:

   ```sh
   sudo -u anygarden anygarden start --data-dir /var/lib/anygarden/alpha \
     --port 8000 --peer-port 8451 --peer-public-url https://alpha.example:8451
   ```

   `--peer-public-url` is the address the *other* node dials (behind a TCP
   proxy: the proxy's address). It only prefills connection cards and invite
   codes. Running the node as the service user matters: credentials created
   by root would be unreadable to it.

   It writes a node root (pinned by peers, 10 years) and a TLS leaf it renews
   itself; see "Certificates and renewal". The node never overwrites or
   rotates a root or a legacy pair. Startup is
   refused for a partial or corrupt pair, and for an
   `ANYGARDEN_PEER_CREDENTIALS_DIR` that holds no pair (an unmounted volume
   must not produce a new identity). An **expired** pair does not stop the
   node: it starts without the federation listener, the node APIs answer
   `CERTIFICATE_EXPIRED` and the connection tab says so (#842). To create the
   pair ahead of time, or to renew it (see below), use
   `anygarden federation init-credentials --data-dir <dir> [--force]`.

   The first user signup on each node is that node's administrator. Repeat for
   the second node with its own data directory.

## Invite → shared channel → delegation

1. Connect the nodes from **Admin → Federation → Connection** (#839). Only two
   strings travel between the admins, and the fingerprint is compared over a
   separate channel (call, chat):
   1. The invited node's admin opens **This node's connection card**, checks
      the address and copies the `agpeer1.` card to the inviting admin. The
      card holds the node ID, certificate and address; no secrets.
   2. The inviting admin pastes it into **Create invitation** and selects
      **Review**. The server shows the node ID, the fingerprint and the IPs it
      resolved for the address. Confirm or edit the IPs, tick **Allow private
      network addresses** for 10.x/172.16–31.x/192.168.x peers, compare the
      fingerprint with the other admin, then pick the channel and
      capabilities. The result is an `aginvite1.` invite code.
   3. The invite code contains the one-time token, expires (default 10
      minutes) and is shown once. Send it privately. The invited admin pastes
      it into **Accept invitation**, reviews the issuer the same way and
      accepts.

   Dialing is limited to the IPs each admin approved; the suggested IPs and
   the URL inside a card or code are only prefill. The equivalent APIs are
   `POST /api/v1/node/connection-card`, `.../connection-card/inspect`,
   `POST /api/v1/node/invites` (returns `invite_code`) and
   `.../invite-code/inspect`, followed by `.../invites/{id}/accept`. Trust is
   certificate-pin based.
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

## Behind a reverse proxy or load balancer

The peer listener authenticates the *client certificate* inside TLS, so a
proxy in front of it must pass TCP through untouched. A proxy that terminates
TLS (nginx `http`/`ssl`, an HTTPS load balancer) drops the client certificate
and every peer request fails with `MTLS_REQUIRED`.

nginx (`stream` module, not `http`):

```nginx
stream {
    server {
        listen 8451;
        proxy_pass 10.0.0.12:8451;   # the node's --peer-port
    }
}
```

HAProxy:

```haproxy
frontend federation
    mode tcp
    bind :8451
    default_backend anygarden_peer

backend anygarden_peer
    mode tcp
    server alpha 10.0.0.12:8451
```

- Advertise the proxy in `--peer-public-url`; the other admin approves the
  proxy's IP, not the node's.
- Certificate pins are unaffected: the node's own certificate still reaches
  the peer end to end.

## Certificates and renewal

Nodes created by `start --peer-port` or `init-credentials` since #843 have a
**root layout** under `<data-dir>/peer`:

| File | Role |
|---|---|
| `peer-root-cert.pem`, `peer-root-key.pem` (0600) | Node root, valid 10 years. Peers pin this certificate; connection cards carry it. |
| `peer-leaf.pem` (0600) | TLS certificate and key in one file, signed by the root, valid 90 days. |

The node renews its leaf itself: at start, at app startup, and every six hours
while running, once it is within 30 days of expiry. The listener serves the
new leaf on new connections without a restart, and peers change nothing,
because their pin is the root. A peer accepts a leaf only when the root it
pinned for *that node* signed it, so another trusted node's root cannot
impersonate this node.

`start` warns 30 days before the **root** expires, and the connection tab
shows the expiry of this node's root and leaf and of every peer's pin.

### Legacy self-signed pairs

Nodes created before #843 keep a self-signed `peer-cert.pem`/`peer-key.pem`
pair (2 years since #842, 90 days before). They keep working and can peer
with root-layout nodes, but must be renewed by hand. Switch one to a root:

1. On that node: `anygarden federation init-credentials --force --data-dir
   <dir>` keeps the node ID, writes the root layout and keeps the old pair as
   `*.legacy`. Restart it and create a new connection card.
2. On each peer: **Replace certificate** next to the node in the trusted-peers
   list, paste the new card, compare the fingerprint, and re-pin.
3. Re-pinning conservatively disables every channel grant and publication
   consent for that peer. Grant them again
   (`PUT /api/v1/node/peers/<node>/grants/<channel>`) and re-approve
   publication on the shared channel.

The same steps replace a root (for example after the root key leaked). A leaf
key leak does not need a new root: until the leaf expires it stays valid, so
revoke the peer (`DELETE /api/v1/node/peers/<node>` or **Revoke**) when it
must stop immediately.

## Failure playbook

- `CERTIFICATE_EXPIRED` (503) from the node APIs: this node's pinned
  certificate (root, or a legacy pair) expired. The node keeps running without
  federation; replace it as above.

- `ENDPOINT_DENIED` while creating or accepting an invitation: the approved IPs
  do not cover the URL host, or they are private and **Allow private network
  addresses** (`allow_private`) is unset.
- `CODE_INVALID` / `IDENTITY_MISMATCH` while reviewing a card or code: it was
  truncated in transit, belongs to this node, or names another node.

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

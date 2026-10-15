# feat(federation): simplify connecting two nodes (#839)

- Commits: `eba6d5d`, `bb4660e`, `ae4542a`, `df6a029`, `98bb037` (squashed in the PR)
- Author: Changyong Um (with Claude)
- Date: 2026-10-15
- Issue: #839

## Situation

Connecting two nodes took several manual steps. Each admin ran
`anygarden federation init-credentials` before `start --peer-port` would boot.
The inviting admin then typed the other node's ID, pasted its full certificate
PEM and entered its URL and IPs. The invited admin pasted the bundle JSON and
typed the issuer's URL and IPs again. The fingerprint was only visible in CLI
output. Checking the UI path found two bugs: creating an invitation always
failed with `INVALID_SCHEMA`, because the client sent `protocol_version` to the
closed `InviteCreate` schema, and private network peers (10.x, 192.168.x) could
not be approved because the UI never sent `allow_private`.

## Task

Reduce the manual steps while keeping mTLS and certificate pinning as the only
peer authentication:

- create credentials on first start without ever replacing an existing identity;
- exchange one pasteable string per hop instead of IDs, PEMs and JSON;
- keep dialing restricted to admin-approved IPs and keep fingerprint comparison;
- document running the peer listener behind a TCP passthrough proxy.

## Action

- `federation/credentials_setup.py`: `ensure_peer_credentials` creates a pair
  only when the default `<data-dir>/peer` holds neither file. It refuses a
  partial or unusable pair, and an explicitly configured
  `ANYGARDEN_PEER_CREDENTIALS_DIR` without a pair (likely an unmounted volume).
  `start --peer-port` uses it and prints the node ID and fingerprint.
  `init-credentials` shares the directory and node-ID helpers.
- `--peer-public-url` / `ANYGARDEN_PEER_PUBLIC_URL`: the address peers dial,
  validated by `split_peer_url`, which `validate_endpoint` now also uses.
- `federation/connection_codes.py`: `agpeer1.` connection cards (node ID,
  certificate, URL) and `aginvite1.` invite codes (the existing bundle plus the
  issuer URL). Decoding is strict and checks that the claimed node ID matches
  the certificate. Suggested IPs come from the literal host or a 3-second DNS
  lookup and are prefill only.
- Admin APIs: `GET /node/identity`, `POST /node/connection-card`,
  `POST /node/connection-card/inspect`, `POST /node/invite-code/inspect`;
  `POST /node/invites` takes `issuer_url` and returns `invite_code`. Inspection
  refuses this node's own card or code and codes meant for another node. The
  peer protocol is unchanged.
- Admin UI: a card for this node, invitation and acceptance by pasting a card
  or code, editable suggested IPs, an explicit "allow private network
  addresses" checkbox and a required fingerprint confirmation. Errors appear in
  the card that caused them, with explanations for the setup codes. Removed the
  `protocol_version` field from invite creation. The node tab's peer and invite
  cards no longer widen past a 375px viewport.
- Requirements FED-06 (revised), FED-10, FED-11, FED-12; runbooks, systemd unit
  notes, delegation doc and CHANGELOG updated.

## Result

- Two real nodes (`anygarden start --peer-port`, private IP 192.168.100.81)
  created their credentials on first start and were connected entirely from
  the UI: card → review → invite code → review → accept. Both sides showed the
  peer as `active` and the invite as `accepted`. Submitting without allowing
  private addresses showed the `ENDPOINT_DENIED` explanation in the invitation
  card.
- Tests: cluster 3587 passed / 7 skipped; vitest 120 files, 891+ tests passed;
  frontend build passed. A new API test completes the card → invite code →
  accept round trip over real mTLS. FED-06/08/10/11/12 are reported as passing.
- Not covered: automatic certificate renewal (90-day validity) and detecting a
  missing default `peer/` directory on a node that already has peers. Both are
  left for follow-up issues.

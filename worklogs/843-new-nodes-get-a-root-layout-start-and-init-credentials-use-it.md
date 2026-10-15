# feat(federation): new nodes get a root layout; start and init-credentials use it (#843)

- Commit: `722dc9c` (722dc9c)
- Author: Claude Opus 5.5
- Date: 2026-10-15T05:38Z
- PR: #843

## Situation

The federation `credentials_setup.py` still created a single self-signed leaf pair (`peer-cert.pem` / `peer-key.pem`). To support renewable leaves and root-pinning (#843), a new node layout was needed: a long-lived root CA certificate plus a short-lived TLS leaf in a single PEM file. The `start` command and app lifespan startup also needed to auto-renew the leaf when it was due, and `--force` needed to replace the root while keeping the node ID and archiving the old pair.

## Task

- Rework `credentials_setup.py` so that:
  - `ensure_peer_credentials` creates a root (`peer-root-cert.pem` / `peer-root-key.pem`) plus a leaf (`peer-leaf.pem`) for new nodes.
  - `--force` regenerates the root (preserving node ID), archives the previous pair as `*.legacy`, and issues a new leaf.
  - Incomplete credential sets (only one of root-cert / root-key present) raise `CredentialSetupError`.
  - Legacy pairs (old-style `peer-cert.pem` + `peer-key.pem`) are still detected and loaded.
- Wire leaf renewal into `anygarden/cli.py` (`start` command) and `anygarden/app.py` (lifespan startup).
- Produce a `check-peers-against-their-pin-root-or-legacy.md` design note.
- Add / update tests across `test_federation_credentials_cli.py`, `test_federation_credentials_setup.py`, `test_federation_root_pin.py`, and `test_federation_cert_expiry.py`.
- All code must pass `ruff format`.

## Action

**`anygarden/federation/credentials_setup.py`** (primary change)

- `ensure_peer_credentials`: detects presence of `peer-root-cert.pem` + `peer-root-key.pem`; if absent, falls through to legacy detection or new-node creation path.
- New-node path calls `create_root_credentials(peer_dir, node_id)` and then `issue_leaf(peer_dir)`.
- `--force` path: calls `create_root_credentials` (keeping node ID from the old root), renames existing cert/key to `.legacy`, issues a fresh leaf.
- Incomplete-set guard (line 110–118): raises `CredentialSetupError` when exactly one of the two root files is present, or exactly one of the two legacy files is present.
- `PeerCredentialFiles` now carries a `kind` field (`"root"`, `"legacy"`, `"leaf"`) so callers can differentiate.

**`anygarden/cli.py`**

- `start` command: calls `ensure_peer_credentials(data_dir)` before starting the listener, triggering leaf renewal if the leaf is due.

**`anygarden/app.py`** (formatting only for this commit's hunks)

- Lifespan block (lines 1421–1522): ruff-reformatted several long SQLAlchemy query expressions and `fanout_*` call sites. No logic change; the lifespan already calls `_compose_federation_services` which invokes `ensure_peer_credentials`.

**`tests/test_federation_credentials_cli.py`**

- `test_init_creates_credentials`: updated assertions to expect `peer-root-cert.pem` + `peer-leaf.pem` instead of the old `peer-cert.pem` / `peer-key.pem`.
- `test_force_replaces_pair_but_keeps_node_identity`: updated to use root cert path; ruff-reformatted the `stat.S_IMODE` assertion into a parenthesised form (line 96–100).
- `test_private_key_and_directory_are_owner_only`: checks mode 0o600 on both `peer-root-key.pem` and `peer-leaf.pem`.
- Existing node-ID-preservation tests updated to read `peer-root-cert.pem`.

**`tests/test_federation_credentials_setup.py`**, **`test_federation_root_pin.py`**, **`test_federation_cert_expiry.py`**: updated or added assertions matching the new file layout and renewal logic.

**`docs/design/check-peers-against-their-pin-root-or-legacy.md`** (68 lines): new design note explaining the dual-mode verification strategy.

8 files changed, 494 insertions(+), 135 deletions(-).

## Decisions

**Root + leaf layout over a single self-signed cert** (from plan §3.1, Decision 1):
- Option A (chosen): long-lived root CA + short-lived leaf signed by root. Renewal is a purely local operation; the peer's pinned root stays valid across leaf rotations.
- Option B: keep SPKI pinning, re-sign with the same key. Rejected — a new self-signed cert differs from the existing trust anchor, and Python 3.12 `ssl` has no verification callback, forcing `CERT_NONE` + manual verification.
- Option C: auto-re-pin via the live mTLS channel. Rejected — requires the peer to be online at renewal time; loses the "works offline" property.
- Decisive factor: only option A makes renewal independent of peer availability.

**`peer-leaf.pem` holds cert + key in one file**: avoids the atomicity problem of replacing two separate files; a single `os.replace` keeps them in sync. Assumption: the file system provides atomic rename — if it does not (cross-device rename), the renewal logic must be revisited.

**Legacy pairs still load without upgrade**: zero-downtime migration. Operators run `init-credentials --force` when ready; until then their old self-signed leaf still works via `pin_matches` legacy branch. Assumption: actual deployments running the old layout are few (UI-based peering was blocked before #839).

**`CredentialSetupError` on partial root set**: half-written credentials are more dangerous than absent ones — a partial set suggests interrupted setup or manual file deletion. Failing loudly prevents silent fallback to an insecure state.

## Result

- New nodes created by `init-credentials` or first `start` get `peer-root-cert.pem`, `peer-root-key.pem`, and `peer-leaf.pem` under `<data-dir>/peer/`.
- `--force` regenerates the root (same node ID), archives old files as `*.legacy`, issues a new leaf.
- Legacy pairs continue to load and are verified via the fingerprint-exact path.
- `start` and app lifespan auto-renew the leaf when it is within the renewal window.
- 204 tests pass (verified in prior step).
- Pending: Step 4 (runtime leaf renewal in the lifespan loop) and Step 5 (listener `refresh_trust` on renewal) are addressed in subsequent commits.

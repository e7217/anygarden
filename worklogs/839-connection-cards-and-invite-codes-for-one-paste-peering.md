# feat(federation): connection cards and invite codes for one-paste peering (#839)

- Commit: `bb4660e` (bb4660e — full SHA unavailable from hook context)
- Author: Claude Opus 5.5 / project contributor
- Date: 2026-10-15
- PR: #839

## Situation

Connecting two Anygarden nodes previously required admins to manually exchange
node IDs, raw certificate PEM blocks, and bundle JSON — each a separate
copy-paste step with no structure or validation at the exchange boundary. This
made federation setup error-prone and inaccessible to non-technical operators,
and left no canonical encoding for the values peers exchange out of band.

## Task

Introduce two opaque, pasteable string formats that bundle the values admins
already exchange:

- **Connection card** (`agpeer1.` prefix): encodes a node's ID, its certificate
  PEM, and the public URL peers should dial.
- **Invite code** (`aginvite1.` prefix): wraps an existing `InviteBundle` plus
  the issuer's public URL.

Constraints:
- The peer-to-peer wire protocol must be unchanged.
- URL and IP suggestions in the code are prefill only; the receiving admin's
  explicit `approved_ips` still gates actual dialing.
- Codes must tolerate whitespace-wrapped pastes (e.g. line-broken email bodies).
- Identity mismatch between a code's embedded certificate and its `node_id` must
  be rejected hard (`IDENTITY_MISMATCH` 400).
- Unknown extra fields in a card must be rejected (closed model).

## Action

**New module `anygarden/federation/connection_codes.py`**:
- Defines `ConnectionCard` and `InviteCode` as `ClosedModel` Pydantic models
  with `field_validator` on `url` / `issuer_url` that calls `split_peer_url`.
- `encode_card` / `decode_card`: base64url-encode/decode a JSON payload with
  `CARD_PREFIX = "agpeer1."`, verify certificate identity on decode.
- `encode_invite` / `decode_invite`: same pattern with `INVITE_PREFIX =
  "aginvite1."`, exposing the secret token from `InviteBundle`.
- `suggest_ips(host)`: async helper — returns the literal IP if `host` is
  already an address, otherwise DNS-resolves with a 3-second timeout, deduplicates
  while preserving order.
- `requires_private(ips)`: flags whether any IP needs `allow_private` approval.
- `_decode` strips all whitespace before parsing, enabling wrapped-paste tolerance.
- `MAX_CODE_CHARS = 32768` guards against oversized payloads.

**New test file `tests/test_federation_connection_codes.py`**:
- Round-trip tests for card and invite (encode → decode → assert fields).
- Malformed-card parametric test (wrong version prefix, wrong kind, bad base64,
  oversized, empty).
- Identity-mismatch test (forged `node_id` in raw payload).
- Unknown-field and bad-URL rejection tests.
- `suggest_ips` tests: literal IP, DNS resolution (monkeypatched), unresolvable.
- `requires_private` boundary tests.

**Updated `tests/test_federation_connection_api.py`**:
- Fixed `# noqa: F401, F811` → `# noqa: F401` on the `pair` fixture import.
- Reformatted several long lines to comply with ruff line-length rules (no logic
  change).

## Decisions

- **Prefix-versioned base64url** over raw JSON or a binary envelope: prefixes
  like `agpeer1.` are self-describing, survive copy-paste without quoting, and
  allow version evolution (e.g. `agpeer2.`) without breaking existing decoders.
  Binary formats were rejected as less paste-friendly. Raw JSON requires quoting
  in shells and is verbose.
- **Closed model (`ClosedModel`)** for `ConnectionCard`: rejects unknown fields
  on decode, preventing an attacker from embedding extra data (e.g. pre-approved
  IPs) that a future reader might act on. `InviteCode` follows the same pattern.
- **`suggest_ips` is advisory only**: IP suggestions are computed server-side for
  UX convenience (prefill the approval dialog) but the receiving admin must
  explicitly confirm them. This keeps the authorization model unchanged; the new
  codes add no new trust surface.
- **3-second DNS timeout**: trades completeness for responsiveness at the admin
  API boundary. Unresolvable hosts return `[]`, letting the admin enter IPs
  manually rather than blocking the request.
- **Token exposed through `model_dump` override in `encode_invite`**: `InviteBundle`
  stores the token as `SecretStr`; the encode path reads `.get_secret_value()`
  explicitly so the secret travels in the code as intended, while the Pydantic
  model still prevents accidental logging elsewhere.

## Result

Admins can now exchange two strings (one connection card, one invite code) to
pair nodes instead of copying three separate structured values. The server
decodes and validates both strings, checks certificate identity, and surfaces
suggested IPs for the admin to approve. The wire protocol and authorization
model are unchanged. A full test suite covering round-trips, malformed inputs,
identity mismatches, and IP suggestion logic was added alongside the module.

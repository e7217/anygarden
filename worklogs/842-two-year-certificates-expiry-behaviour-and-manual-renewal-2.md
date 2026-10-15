# docs(federation): two-year certificates, expiry behaviour and manual renewal (#842)

- Commit: `42b6abe` (42b6abec0a9fad47478db04e8aa69bff6238a306)
- Author: Changyong Um
- Date: 2026-10-15T14:16:14+09:00
- PR: #842

## Situation

Federation peer certificates were previously issued with a 90-day validity
period, which proved too short: operators frequently encountered expired
certificates that halted the entire node. The existing startup logic treated an
expired certificate the same as a corrupt or missing one — refusing to start —
leaving the cluster completely inaccessible until manual intervention. No CLI
warning was emitted before expiry, and the runbook had no renewal procedure.

## Task

Update all user-facing documentation to reflect the new certificate lifecycle
behaviour introduced in #842:

- Extend default certificate validity from 90 days to 2 years.
- Document that an expired-but-intact certificate no longer stops the node
  (it starts without the federation listener, APIs return `CERTIFICATE_EXPIRED`).
- Add a "Certificate renewal" runbook section with step-by-step CLI + curl
  instructions for renewing this node and re-pinning on each peer.
- Add requirements FED-13 and FED-14 for the new expiry behaviour and 30-day
  warning.
- Update CHANGELOG with the `### Fixed` and `### Changed` entries for #842.

## Action

**`docs/runbook/federation-two-node.md`** (lines 70–83, 161–199):
- Updated the certificate lifetime description from "90 days" to "2 years
  (pairs created before #842: 90 days)".
- Added the new grace-degradation behaviour: expired pair → node starts without
  federation listener, APIs answer `CERTIFICATE_EXPIRED`.
- Inserted new `## Certificate renewal` section covering:
  - `anygarden federation init-credentials --force` on the renewed node.
  - Per-peer re-pinning via `POST .../certificate` REST call with example.
  - Follow-up steps: re-grant channel permissions and publication consents.
  - Key-leak revocation path.
- Added `CERTIFICATE_EXPIRED (503)` entry to `## Failure playbook`.

**`docs/runbook/local-node.md`** (lines 149–162):
- Changed "unusable (corrupt, expired, mismatched)" to "unusable (corrupt, not
  yet valid, mismatched)" — expired is no longer a startup-refusal condition.
- Added bullet documenting the new expiry grace behaviour and the 2-year
  validity, with cross-reference to the two-node runbook.

**`docs/requirements.md`** (lines 182–183):
- Added FED-13: expired certificate → node starts, federation off, API/UI
  report `CERTIFICATE_EXPIRED`.
- Added FED-14: 30-day pre-expiry warning in CLI and admin UI.

**`packages/cluster/CHANGELOG.md`** (lines 5–20, 67–71):
- `### Fixed`: expired certificate no longer stops the node; `--force` now
  keeps the node ID even for an expired pair.
- `### Changed`: new certificates are valid for 2 years; 30-day warning and
  expiry display in connection tab.

## Decisions

- **Graceful degradation over hard failure**: the core implementation choice
  (accepted elsewhere in #842) was to let the node run in a degraded mode
  rather than refuse to start. Documentation mirrors this explicitly so
  operators don't assume the old "start fails = check certs" mental model.
- **Manual renewal procedure**: renewal remains manual pending #843 (per-node
  root with auto-renewed leaf). The runbook documents the current curl-based
  workaround rather than waiting for automation, so operators are not blocked.
- **Keep node ID on `--force`**: the `init-credentials --force` behaviour was
  fixed to preserve the node ID even for expired pairs. This is documented as
  a corrected semantic, not a new feature, to set operator expectations correctly.
- **Assumption**: the REST endpoint `POST .../peers/<id>/certificate` exists
  with the documented request shape. If that API changes before #843 ships,
  the renewal runbook must be updated.

## Result

All four documentation files now consistently describe:
- 2-year certificate validity for new pairs (existing pairs unchanged until renewed).
- Graceful degradation on expiry (federation off, APIs report `CERTIFICATE_EXPIRED`).
- A step-by-step manual renewal procedure.
- Requirements FED-13 and FED-14 added for test coverage tracking.
- CHANGELOG updated for the upcoming release.

No code changes in this commit; tests are tracked by FED-13/FED-14 in separate
implementation commits. The `--amend` fix for `init-credentials --force` on
expired pairs is recorded in CHANGELOG but implemented in prior commits.

# docs(federation): root layout, automatic leaf renewal and switching legacy pairs (#843)

- Commit: `f38a04b` (f38a04b966936e9acd6e9daff9974cdb411aea15)
- Author: Changyong Um
- Date: 2026-10-15T14:46:52+09:00
- PR: #843

## Situation

Federation certificate management was documented as a fully manual process: operators had to run `init-credentials --force`, restart the node, create a new connection card, and re-pin the certificate on every peer via a raw `curl` call. There was no description of the root-layout credential structure introduced by #843, no guidance on the distinction between legacy self-signed pairs and the new root layout, and the requirements table had no entries covering automatic renewal or the anti-impersonation guarantee.

## Task

- Replace the "Certificate renewal" runbook section with a comprehensive "Certificates and renewal" section covering the root-layout file structure, automatic leaf renewal behaviour, and the legacy-to-root migration workflow.
- Update `local-node.md` to describe the new root layout and leaf renewal alongside the existing expired-pin behaviour.
- Add requirements FED-15, FED-16, FED-17 to `docs/requirements.md`.
- Add a CHANGELOG entry under `### Added` in `packages/cluster/CHANGELOG.md`.

## Action

**`docs/runbook/federation-two-node.md`**
- Renamed `## Certificate renewal` → `## Certificates and renewal`.
- Replaced the manual curl-based re-pin procedure with a table of the three root-layout files (`peer-root-cert.pem`, `peer-root-key.pem`, `peer-leaf.pem`) and a prose description of automatic leaf renewal (at start, app startup, every 6 hours within 30 days of expiry).
- Added `### Legacy self-signed pairs` sub-section: explains legacy pairs still work, and gives a 3-step procedure (init-credentials --force → restart + new card → Replace certificate in UI → re-grant channel grants) for migrating one node to the root layout.
- Clarified that the same steps apply to replacing a leaked root, and that a leaf key leak only requires peer revocation.
- Updated `CERTIFICATE_EXPIRED` failure-playbook bullet to say "root, or a legacy pair" instead of just "certificate".
- Updated the `init-credentials` description in the setup section to reference the new root layout instead of the 2-year validity note.

**`docs/runbook/local-node.md`**
- Changed "Peer credentials live under `<data-dir>/peer/` as `peer-cert.pem` + `peer-key.pem`" → "(layout below)" to defer to the two-node runbook.
- Added a new bullet describing the root layout files and the automatic leaf-renewal cadence.
- Retained the existing `#842` expired-pin bullet with wording updated to say "pin" instead of "pair".

**`docs/requirements.md`**
- Added FED-15 (automatic leaf renewal, peers unaffected), FED-16 (anti-impersonation: another node's root cannot sign for this node), FED-17 (legacy ↔ root interoperability and re-pin workflow).

**`packages/cluster/CHANGELOG.md`**
- Prepended a new bullet under `### Added` summarising all #843 federation certificate features: root layout, automatic leaf renewal, pin matching, legacy pair compatibility, `init-credentials --force` migration, `GET /api/v1/node/identity` fields `pin_kind` and `leaf_expires_at`.

## Decisions

- **Merge renewal mechanics into a single runbook section** rather than keeping a separate "Certificate renewal" heading: the old section described only manual renewal; once automatic renewal exists, a separate heading would imply the manual procedure still applies. A unified section with a `### Legacy self-signed pairs` sub-section keeps both paths readable without duplication.
- **Reference the two-node runbook from local-node.md** rather than duplicating the table: the root-layout detail belongs where federation is fully explained; local-node.md needs only enough to point operators in the right direction.
- **Three new requirements (FED-15–17)** rather than amending FED-13/FED-14: the new behaviours (auto-renewal, anti-impersonation, legacy interop) are distinct testable properties that did not exist before #843. Adding them as new rows preserves traceability without retroactively expanding older requirements.
- **CHANGELOG entry position**: prepended before existing entries under `### Added` to keep newest-first ordering consistent with the rest of the file.

## Result

- Runbooks now accurately reflect the root-layout credential structure and automatic leaf-renewal behaviour shipped in #843.
- Operators have a clear 3-step migration path from legacy self-signed pairs to the root layout without losing node identity or peer connectivity.
- Requirements table covers the three new testable behaviours (FED-15–17) that acceptance tests must verify.
- CHANGELOG entry gives a complete feature summary for the release notes.
- No code changes; documentation and requirements only.

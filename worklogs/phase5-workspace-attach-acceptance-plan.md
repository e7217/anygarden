# Phase 5 workspace-attach acceptance plan

## Scope and safety boundary

Phase 5 validates an administrator-approved attachment capability.  The test
surface must never attach or write a real external workspace.  All local
workspace registration, root enforcement, filesystem change records, consent
receipts, and signed audit receipts use deterministic fakes/fixtures.

The cluster boundary receives only opaque identifiers and redacted metadata:
it must not persist, echo, or audit a raw host path, source content, prompt,
stdout/stderr, environment value, or secret.

## Contract matrix

| Area | Acceptance cases | Expected result |
| --- | --- | --- |
| Registration and consent | Raw path in cluster input; non-admin; missing local consent; fingerprint mismatch; unsupported daemon capability | Reject before attach activation; no attachment or execution record; redacted deny audit only. |
| Attachment identity | Opaque workspace ID; `(machine, agent, room, participant)` binding; second active attachment; read/write mode and expiry ceiling | IDs and hashes only in API/WS payloads; one active attachment per agent; invalid state transition rejected. |
| Root enforcement | Absolute/`..` path; symlink/reparse escape; `.git` and credential/config targets; allowlist hash mismatch | Fake adapter rejects; no external I/O; audit contains only relative/redacted outcome metadata. |
| Write authority | Restricted engine; engine without root enforcement/audit signing; no source-linked claimed Task/Turn; wrong room/observer | Fail closed before lease/send; pending turn cancelled with an attributable redacted audit decision. |
| Lifecycle fence | Revoke, archive, removal, or expiry during a lease; stale completion/retry; reconnect at old epoch | Server and daemon reject stale work; no retry/replay; process-stop receipt and audit chain are recorded. |
| Audit | Request/decision/start/finish/revoke chain; prompt/secret/path/output probes; receipt/hash tamper probe | Immutable ordered chain validates; redacted projection contains no prohibited values. |
| Transport/client | REST authorization and canonical error status; WS event/reconnect epoch; Python/TS agent-machine handoff and legacy daemon | Opaque payloads only; legacy/unsupported daemon fails closed; client does not retry a revoked attachment. |

## Planned suites after the implementation head is published

1. Cluster service/API regressions for attachment state, authority, task/turn
   binding, audit chain, archive/removal/expiry/revoke, and no raw-path leak.
2. WebSocket frame and replay/reconnect tests for opaque `workspace_id`,
   `attachment_epoch`, mode, allowlist hash, stale fences, and silence after
   revocation.
3. Machine fake-adapter tests for local consent/fingerprint/capability checks,
   root enforcement denial, stop receipt, and old-daemon rejection.
4. Python and TypeScript protocol tests for exact handoff/revoke behavior.
5. Browser E2E only if the implementation adds an administrator UI.  It will
   mock the cluster/machine boundary and assert no real filesystem operation.

## Exact-head command template

The filenames will be replaced with the submitted paths, then executed on the
implementation SHA:

```bash
uv run --package anygarden --extra dev pytest \
  packages/cluster/tests/test_workspace_attachments.py \
  packages/cluster/tests/test_workspace_attachment_authorization.py \
  packages/cluster/tests/test_workspace_attachment_ws.py -q

uv run --package anygarden-machine --extra dev pytest \
  packages/machine/tests/test_workspace_attach.py -q

uv run --package anygarden-agent --extra dev pytest \
  packages/agent/tests/test_workspace_attachment_protocol.py -q

cd packages/agent-ts && npm test && npm run lint && npm run typecheck
```

The implementation submission must name its actual test paths and expose a
fake-only adapter seam before this template is treated as executable evidence.

# Authenticated agent execution control

The approved transport reuses the representative agent's existing DM WebSocket. The cluster coordinates durable acceptance and launch intent; only the agent creates a local Invocation. The server-only package must not import agent runtimes. There is no machine-daemon or cluster-host fallback.

| Condition | Behavior |
| --- | --- |
| Current canonical DM, advertised capability, matching generation | Bounded correlated prepare/start/reconcile/cancel/revoke requests |
| Legacy agent, missing DM, disconnected or replaced socket | Unavailable; never launch elsewhere |
| Prepare | Persist a bounded request and immutable fingerprint; no manager start or CLI process |
| Start after authority acceptance | Rebuild from current trusted launch; same prepared fingerprint and generation required |
| Duplicate start or lost acknowledgement | Same execution ID returns its durable receipt; no new process |
| Restart before start | Prepared request survives; changed generation/config refuses start |
| Restart after launch intent | Existing manager receipt is reconciled; unknown execution is never replayed |
| Cancel/revoke before start | Durable tombstone prevents delayed start |
| Disconnect, revoked policy, expired control lease | Revoke local scope and stop running process; report only confirmed termination |
| Old generation receipt | Current authenticated agent may inspect/cancel its own local receipt, but cannot start it |
| Codex native | Stage only bounded local auth.json into private isolated home; no host config, rules, MCP or server bearer |
| Codex direct / Pi native or direct | Bind only current local launch credentials; reuse strict endpoint/auth materialization |
| Trusted permission or unsupported platform | Refuse remote execution; do not silently broaden or substitute the room runtime |
| Pi restricted | Explicit read/grep/find/ls tools; no self-tools/extensions. This is not an OS filesystem sandbox |

Wire prepare carries execution and authority/channel identity, prompt, and policy/grant/peer epochs. The agent supplies actual workspace, opaque workspace binding, engine/version, model/provider, permissions and credential material. It returns execution_id, scope, fingerprint and generation only. Later commands carry execution_id and fingerprint; responses contain the public Receipt fields, never native session handles or local paths.

Prepared state is distinct from the manager's queued state: manager.start immediately schedules work and cannot implement prepare. Private SQLite records retain only request, safe invocation descriptor and hashes; raw environment and credentials are rebound in memory. The existing receipt manager continues to mark interrupted launches unknown instead of restarting them.

The broker fences each pending request by agent identity, canonical participant, DB generation, live socket epoch, action and request ID. General chat send/metadata cannot complete a control request. A short lease is renewed only through freshly authorized start/reconcile requests. The cluster must revoke rather than renew after authority checks fail; the agent independently stops on connection loss or lease expiry.

Tests use isolated temporary storage and fake local CLI executables; no user credentials or billed model calls. Required checks include no launch during prepare, persisted prepare/start, duplicate and restart behavior, disconnect/expiry stop proof, strict credential isolation, stale/forged websocket responses, and server-only imports.

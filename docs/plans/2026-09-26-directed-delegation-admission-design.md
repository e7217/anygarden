# Directed delegation admission

This implements the approved platform completion design's `/delegate` path. The SDK addresses one agent with `[DELEGATED] <@user:participant-id> task` and includes `delegation_id` plus `delegation_target_participant_id`. The server admits that explicit target through the existing durable turn transaction instead of treating the delegation as room chatter.

| Condition | Admission and delivery |
| --- | --- |
| Agent sender, UUID delegation ID, canonical leading target mention, another executable agent participant in this room | Create one fresh child request, attempt, lease and outbox; preserve the delegation ID |
| Human/guest sender, malformed contract, missing/foreign/observer/human/self target | Return a WS error before storing or dispatching the request |
| Round-robin, orchestrator, or thread send | Explicit validated target owns nomination; no fallback or additional mentioned agent receives a child turn |
| Concurrent explicit delegations | Each valid request gets its own child turn; the heuristic peer-chatter mention budget does not discard explicit requests |
| Live delivery or reconnect replay | The target receives execution through the durable outbox; raw room broadcasts/replay do not also wake it |
| Messages without the new target metadata | Preserve existing routing and legacy delegation behavior |

Validation belongs in the message transaction and uses server-parsed content and current room participation. A child never reuses the parent's request ID. Endpoint tests use real WebSocket frames and an in-memory database to verify admission, persisted turns, leases, rejection, and delivery boundaries.

Terminal result messages from authenticated agents preserve the original request completion proof and stored result, but never nominate another speaker or create a thread/peer turn. Result subscribers consume these control replies without launching an engine. Ordinary and nonterminal messages retain their routing.

SDK connections opt into `?ready=1`. After subscription, history replay and pending durable delivery, the server emits `{type: "room_ready", room_id}`. The SDK uses this explicit barrier before forwarding a delegation into a newly joined room. Existing connections without the option keep their current frames.

Public message ingress reserves durable invocation proof. REST rejects the directed target contract and strips turn/lease/workspace proof, caller nominations, and terminal delegation control outcomes. WS human/guest messages strip the same proof and control outcome; authenticated agent completions are validated first, then their execution proof is removed from broadcasts/history while the echoed request ID remains for tracing. Only durable outbox delivery attaches a recipient's executable lease.

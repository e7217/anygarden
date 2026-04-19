# feat(cluster,agent): wire ingest_only broadcast + agent opt-out (#148 Part 3)

- Commit: (pending — see PR)
- Author: Changyong Um
- Date: 2026-04-19
- PR: follow-up to Parts 1 (#149) + 2 (#150)

## Situation

Parts 1 and 2 added the two DB flags (`rooms.context_window_enabled`, `agents.context_window_opt_out`) and the UI toggles that read/write them, but left the runtime paths untouched — the flags were pure storage. Part 3 finally wires them so the toggle actually changes behaviour: the cluster stamps `metadata.ingest_only=True` on ambient broadcasts in opt-in rooms, and opted-out agents demote those broadcasts from `INGEST_ONLY` to `SKIP` in `decide_policy`. Stage B's env-gated path still coexists (removed in Part 4) so there's no flag cutover risk.

## Task

- Server: add `_is_ambient_candidate()` and attach `ingest_only` on the broadcast path when `rooms.context_window_enabled` is True.
- Server: thread the agent's `context_window_opt_out` into `WelcomeOut` so the SDK can cache it on every ws connect.
- Agent: cache `_context_window_opt_out` on `ChatClient` from the welcome frame; teach `decide_policy` to SKIP ingest_only messages for opt-out agents (keeping addressability wins).
- Tests: ws-handler side covers {flag-on ambient, flag-off ambient, direct-mention bypass, welcome carries opt-out}; agent-side covers {opt-out demotes to SKIP, opt-out doesn't override direct mention}.
- No Stage B removal yet — Part 4 owns the env/accumulator cleanup.

## Action

### Server (cluster)
- `ws/protocol.py::WelcomeOut` — new `context_window_opt_out: bool = False` field so the SDK can read-once at welcome.
- `ws/handler.py` — welcome path does a single `Agent.context_window_opt_out` `SELECT` inside the existing session for agent connections; `agent_opt_out` is defaulted False for user/guest welcome frames.
- `ws/handler.py::_is_ambient_candidate()` — pure helper mirroring `decide_policy` rules 5/7 so the "server-side stamp" and "agent-side read" stay symmetric. Rejects `[DELEGATED]`/`[ROOM_QUERY]` prefixes, `room_query` metadata, and any `user`/`legacy` mention. Docstring explicitly calls out the required symmetry.
- `ws/handler.py` broadcast path — on each `SendFrame`, fetch `rooms.context_window_enabled` once (part of the same DB session that writes the message) and stamp `metadata["ingest_only"] = True` on candidates. Uses `"ingest_only" not in metadata` so representative-agent paths that already set the flag (e.g. `room_query._deliver_result`) stay idempotent.

### Agent
- `client.py::ChatClient` — `_context_window_opt_out: bool = False` default. Welcome handler now reads `context_window_opt_out` off every welcome frame (not only when set, to allow downgrade from True to False too).
- `integrations/base.py::decide_policy` — rule 4 now branches on `getattr(client, "_context_window_opt_out", False)`: opt-out agents return `SKIP`, others keep the pre-existing `INGEST_ONLY`. Addressability (rule 3) still wins above this, so a direct mention on an opted-out agent still gets `RESPOND`.

### Tests
- `cluster/tests/test_ws_handler.py::TestContextWindowBroadcast` — 4 new cases. Default flag off: no stamp. Flag on + ambient: stamp. Flag on + direct mention: no stamp. Opt-out agent welcome: carries `context_window_opt_out=True`.
- `agent/tests/test_integrations/test_should_respond.py::TestDecidePolicy` — 2 new cases: `test_opt_out_agent_skips_ingest_only_broadcast` (opt-out demotes, default ingests), `test_opt_out_does_not_override_direct_mention` (addressability wins). `_make_client` helper extended with `context_window_opt_out` keyword defaulting to False.

## Decisions

### Propagation of the agent opt-out: welcome-frame cache vs. CLI plumbing
- A. Thread `context_window_opt_out` from `agents.context_window_opt_out` → `SyncDesiredStateFrame` → `SpawnManifest` → `--context-window-opt-out` CLI arg → `ChatClient.__init__` parameter. Matches `reasoning_effort`'s existing path.
- B. Return the flag in `WelcomeOut`; the SDK caches it on every welcome → **chosen**.

Rationale: (A) touches 5 layers (cluster protocol, machine protocol, spawner, agent CLI, ChatClient) for a 1-bit value that only the SDK cares about. (B) touches 2 (welcome schema, SDK welcome handler) and gets one additional nice property: admin toggles that happen while the agent is running propagate on the very next ws reconnect — no spawner restart needed. Part 2 already calls `bump_generation` on toggle, so the agent will reconnect within its normal respawn window anyway, which means (B) is strictly faster to reflect than (A). The plan §3.1 explicitly said "agent startup cache, restart required"; (B) satisfies that with less surface.

Observation retained for later: if we ever want live runtime toggles (no restart) we can add a dedicated server-push frame, but that's a separate ticket. Welcome-on-reconnect is fine for Stage B decommission.

### Server-side ambient classification duplicates `decide_policy` rules 5/7
Acknowledged in plan §6 "리스크 및 고려사항 / 중간". Mitigation applied:
- Added a docstring note on `_is_ambient_candidate()` that it must stay symmetric with `decide_policy` rule 5/7.
- Kept both checks lexically simple (mention types, prefix checks, `room_query` presence) so drift is easy to spot in review.
- Tests on both sides use the same fixture content (e.g. `"@bot 핑"` for mentions) so a drift produces visible asymmetric failures.

Future refactor: extract `parse_mentions` + the ambient predicate into a shared module importable by both packages. Out of scope for Part 3 because agent package importing cluster would invert the dependency direction, and moving it to a shared crate is a bigger lift.

### Stamping skipped when `ingest_only` is already present
- `room_query._deliver_result` stamps `ingest_only=True` on `[취합 결과]` broadcasts. When it lands in a `context_window_enabled=True` room, the new server path could *re-*stamp — harmless in value terms (True==True) but hides a future bug if the producer's semantics change. The explicit `"ingest_only" not in metadata` check makes intent obvious.

### Stage B env + accumulator left in place
- Part 4 owns the cleanup; keeping both paths in Part 3 means a Part 3 regression can be rolled back without losing Stage B's safety net. When Part 4 merges, the env + accumulator go away and `_ambient_capture_enabled()` is inlined out of `decide_policy`.

## Result

- `rooms.context_window_enabled=True` rooms now produce `ingest_only` stamped broadcasts for ambient messages, and `decide_policy` in the agent respects both the stamp and per-agent opt-out.
- Agent reconnect refreshes the opt-out cache from `WelcomeOut` so a Part 2 toggle takes effect on the next `bump_generation` respawn (no machine restart, no CLI rewiring).
- Tests: cluster 586, agent 173, machine 232 — all pass. 6 new cases land across `test_ws_handler.py` and `test_should_respond.py`.
- Stage B coexists untouched — Part 4 will collapse the two paths into the server-driven one.

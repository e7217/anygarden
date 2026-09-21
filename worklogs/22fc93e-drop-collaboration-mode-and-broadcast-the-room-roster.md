# refactor(agents): drop collaboration_mode, broadcast the room roster, surface the introduction (#644)

- Commits: `6b2377a`, `26654bc`, `d582930`, `22fc93e`
- Author: Changyong Um
- Date: 2026-09-22
- Issue: #644
- Supersedes: #279 (`584eed3`)

## Situation

A review question — "wasn't the feedback that `collaboration_mode` is no longer needed?" — turned into an audit of what the column actually does. Three findings, each verifiable in the code rather than in intent:

1. **It was never a safety control.** The peer-mention safety net (`ws/handler.py`, `MAX_PEER_DEPTH` + `PeerHandoffBudget`) never reads `agents.collaboration_mode`. `is_peer_mention()` only asks whether the sender is an agent and the target is an agent participant. A `solo` agent that emitted `<@user:uuid>` still woke its peer. The column withheld *guidance*, not permission.

2. **Only one engine of four honoured it.** `openhands_engine.py` hardcoded `include_roster=True, with_collaborative_hint=True`; `claude_code.py` overrode the gate for orchestrators; `codex_cli.py` gated the roster but (since #540) injected the system prompt unconditionally; `gemini_cli.py` alone matched the design.

3. **Changes never reached a running agent.** `_collaboration_mode_by_room` and `_participants_by_room` were populated only from the `welcome` frame; the `room_settings_changed` handler refreshed dispatch fields but not the roster. `api/v1/agents.py` documented the limitation in a comment ("Peers pick up the new value on their next welcome"). Meanwhile `OverviewPanel.tsx` told admins the toggle "takes effect without a respawn". Already logged as P1 in `docs/e2e/2026-07-31/findings.md:167-184`.

A fourth observation decided the shape of the fix. The roster line is `- {name} (id: {uuid}, kind: {kind}) — {description}`, so `Agent.description` is the only basis a teammate's model has for choosing whom to ask. The live fleet confirmed it: the three agents with written introductions (`pm`, `dev`, `qa`) were exactly the three set to `collaborative` and the ones used to validate collaboration; the three `solo` ones had no description at all. The axis that mattered was never the toggle.

## Task

- Make the roster propagate at runtime, **before** removing anything — the removal directs admins to write introductions, and an introduction that doesn't reach peers would make that advice actively misleading.
- Tell admins why the introduction matters, at the point of editing.
- Remove `collaboration_mode` end to end and make the roster unconditional, keeping the peer-mention budget as the real cap.

## Action

- **Roster broadcast (`6b2377a`)** — new `rooms/roster.py` holding `build_participants_brief` (moved out of `ws/handler.py`; hosting it in `rooms/membership.py` would have closed an import cycle, since the handler already imports that module) plus `broadcast_roster` / `broadcast_roster_for_agent`. Wired into `add_participant`, `remove_participant`, and `PUT /api/v1/agents/{id}` behind a new `roster_changed` flag set by `name` and `description` edits — not by avatar edits, which `ParticipantBrief` doesn't carry. Rides `RoomSettingsChangedOut` rather than a new frame: that frame already carries welcome-cached room state (`ephemeral`, `context_window_enabled`) to exactly this audience, so `participants` is the same kind of value with the same `None` = "not touched" rule. Full snapshot, not a delta — idempotent, and a merge would retain peers who left.

- **SDK refresh (`26654bc`)** — `client.py` consumes `participants` from `room_settings_changed`, replacing the cache wholesale. Extracted `_cache_roster` so the welcome and update paths share one well-formedness guard (entry must be a dict with a truthy `id`; a malformed neighbour doesn't abort the refresh).

- **Introduction UX (`d582930`)** — concrete example as placeholder, plus an inline flag while the field is empty. Caution orange, not danger: a missing introduction degrades routing rather than breaking anything, which is the split `DESIGN.md` §2 established in #435. No icon, keeping it quieter than the adjacent error row.

- **Removal (`22fc93e`)** — migration `072` drops the column; `WelcomeOut.my_collaboration_mode`, the handler lookup, the three REST schema sites, the SQLAlchemy mapping, the SDK cache and `is_collaborative()`, the `with_collaborative_hint` / `include_roster` parameters, and the admin UI select all go with it. `claude_code.py` keeps `is_orchestrator` — it still gates `handoff_to` MCP exposure, which is a real authority boundary, unlike knowing who is in the room.

- **Tests** — `test_collaboration_mode.py` was nearly deleted wholesale before noticing it held the WS-handler peer-mention integration tests alongside the welcome-stamping ones. Restored, split: the welcome half removed, the rest renamed to `test_peer_mention_safety_net.py` and left otherwise untouched, where it now serves as the evidence that the cap never depended on the column. New `test_room_roster_broadcast.py` (6 tests) covers membership add/remove, description and name propagation, and the avatar-edit silence. `test_speaker_strategy_welcome.py` gains 4 SDK tests including one asserting the refreshed roster reaches the rendered prompt. In `test_claude_code.py`, `test_non_orchestrator_prompt_unchanged` was retargeted at the only case that still yields an untouched prompt — an empty roster — and the `solo` twin deleted as redundant.

## Result

- **cluster**: 1774 passed, 1 failed. The failure is `test_audit_flush_releases_callers_before_second_pool_checkout`, which fails identically on `main` (verified by running main's copy under its own venv) — a pre-existing baseline failure in audit/pool-capacity handling, untouched by this change.
- **agent**: 587 passed.
- **frontend**: 528 passed across 56 files; `npm run build` (vite + tsc) clean.
- **Migration**: `upgrade head` → `downgrade -1` → `upgrade head` round-trip verified against `packages/cluster/anygarden.db`, column present/absent at each step as expected. Eleven tests hardcoding `071_interaction_resolutions` as head were updated to `072` — the assertion comments say this is intended to require updating.
- **Lint**: the repo has no ruff configuration, so `ruff check packages/` runs the default ruleset against a baseline of ~1580 pre-existing violations. The touched files were checked with `--select F,E9`: clean, after removing one `selectinload` import left unused by the roster move.

Behavioural impact:
- The three agents previously on `solo` (`qa_agent`, `테스트에이전트01`, `테스트에이전트02`) now receive the roster and the peer-mention hint. None has a description, so their roster lines carry a bare name until one is written — which is what the new UI flag exists to prompt.
- Peer-ask frequency should rise in multi-party rooms; 1:1 DMs are unaffected (the roster is one human). `MAX_PEER_DEPTH=1` and the per-turn budget still cap it, and the now-universal hint carries the "don't peer-ask over trivia" brake that `solo` agents previously never saw.
- A membership change or an edited introduction now reaches connected agents without a reconnect, closing the P1 from the 2026-07-31 E2E findings.
- "Keep this agent from calling peers" is no longer expressible as a per-agent flag. It was never enforced as one; room-level `speaker_strategy` and `AGENTS.md` remain the honest ways to say it.

Deliberately out of scope: `speaker_strategy` / the `orchestrator` strategy (v4 federation work — `docs/plans/2026-07-27-machine-federation-design.md` §4.2 anticipates simplifying the same condition), peer-budget constant tuning, and federated rosters crossing machine boundaries.

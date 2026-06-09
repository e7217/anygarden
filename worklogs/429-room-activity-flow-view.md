# feat(observability): Phase 3 — room activity flow view (#429)

- Commit: `cf6ad65` (cf6ad65 on branch feat/429-instrumentation-phase3)
- Author: Changyong Um
- Date: 2026-06-09
- PR: #429

## Situation

Phases 1 (#425) and 2 (#427) made the instrumentation data consumable and added
the `/api/v1/rooms/{id}/activity` endpoint + indexed `room_id` column. Phase 3 is
the "weave the multi-agent flow" payoff the user asked for. The per-agent
ActivityPanel only shows one agent at a time; there was no room-level view of how
multiple agents' turns interleave.

## Task

- Add a room-level, admin-only view of all agents' turns in a room, consuming the
  Phase 2 endpoint and reusing the existing turn-grouping.
- Decide honestly what of the roadmap's Phase 3 is safely shippable now vs. needs
  a design decision.
- Constraints: reuse ActivityPanel's `splitLogs`/outcome display; admin-gate
  (the endpoint is admin-only); follow the existing room-dialog mount pattern;
  no backend change.

## Action

- `agent-settings/ActivityPanel.tsx`: exported `ActivityLog`/`Turn`/`splitLogs`/
  `turnLabel`/`turnDotClass`/`formatDuration`; added `agent_id` to the row interface
  and `agentId` to `Turn` (first non-null `agent_id` across the turn's events) so a
  room view can label each turn by owning agent.
- `components/RoomActivityDialog.tsx` (new): fetches `/api/v1/rooms/{id}/activity`
  once on open, groups via `splitLogs`, renders turns newest-first with agent badge,
  outcome dot/label, authoritative duration, engine, and error.
- Mount chain (mirrors Artifacts): `RoomSettingsMenu` gains an `onShowRoomActivity`
  prop + "Room activity" item; `RoomHeader` threads the prop through; `ChatPage`
  adds `roomActivityOpen` state, passes the callback only when `user?.is_admin`, and
  renders `<RoomActivityDialog>` behind the same admin gate.
- Tests: `ActivityPanel.test.ts` gains an `agentId`-capture case.

## Decisions

- **Reuse `splitLogs` (room-scoped) rather than a new grouping.** Same request_id
  bookkeeping; only the owning agent needed surfacing. Added `agentId` to `Turn` and
  kept the per-agent panel unchanged (it just ignores the field).
- **A→B causal links deferred, not faked.** Investigation found the request_id model
  only tracks USER-triggered turns — agent-to-agent turns get no request_id /
  message_received, and naively extending the fan-out to every agent on each agent
  send would mint request_ids for SKIP-policy recipients, flooding ActivityLog with
  phantom-orphan turns. So real causal linking needs a tracking-model decision; I
  reverted the speculative cache/span-link infra rather than ship inert code that
  implies a working feature.
- **per-engine LLM detail deferred (roadmap-optional).** It's a cross-package
  `run_engine` return-contract change (str → (text, usage)) re-touching all four
  adapters immediately after #422 — the optional capstone, gated behind a checkpoint.
- **Admin gate + 1-fetch (no polling)** matches the per-agent ActivityPanel and the
  admin-only endpoint. Assumption: a single fetch is acceptable for a debug view;
  live updates are out of scope.

## Result

- Full frontend suite 428 passed (46 files, incl. RoomHeader/RoomSettingsMenu);
  `tsc -b` clean. Frontend-only — no backend change, cluster suite unaffected.
- Admins get a "Room activity" menu item opening a dialog that shows every agent's
  turns in the room, newest-first, with agent/outcome/duration/engine/error — the
  multi-agent flow in one place.
- Pending (follow-ups, by design): A→B causal links (needs an agent-triggered-turn
  tracking decision) and the optional per-engine LLM detail capture. With these
  deferred, the gateway/Langfuse adoption question can be revisited on top of the
  now-much-richer gateway-free instrumentation.

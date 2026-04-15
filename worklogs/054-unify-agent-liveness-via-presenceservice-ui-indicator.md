# feat(presence): unify agent liveness via PresenceService + UI indicator (#54)

- Commit: `d0efbcf` (d0efbcfa2026bf748e2a72c00e04e67f6cbc6745)
- Author: Changyong Um
- Date: 2026-04-16T01:02:11+09:00
- PR: #54

## Situation

"Is this agent reachable right now?" had three different answers. `ConnectionManager` tracked WebSocket subscriptions, `Agent.last_heartbeat_at` reflected the machine-daemon heartbeat, and REST/UI paths just read the `Participant` row with no liveness signal at all. The mismatch showed up most painfully in `[ROOM_QUERY]`: a dead agent was still counted toward `expected_count`, so every cross-room query waited the full timeout and then reported `(1/2) — 1명 미응답`. Users had no way to tell "agent is slow" from "agent is gone".

## Task

- Introduce one service that answers the liveness question end-to-end.
- Plumb presence through REST responses, WS broadcasts, cross-room query expected-count, and three frontend consumers (popover, header, representative dropdown).
- Preserve backward compat so callers that don't know about presence keep working.
- Stay clear of `#53` (already merged) and `#55` (concurrent, touching the same `room_query.py` and tests) — let `#55`'s structural `_deliver_result` refactor land and integrate around it.

## Action

- Added `doorae.presence.PresenceService` (`packages/cluster/doorae/presence/service.py`) with a three-tier resolution: WS subscription → `Agent.last_heartbeat_at` fallback → disconnect memo. `room_snapshot` uses a single `IN`-query for agent heartbeats to avoid N+1.
- Wired `ConnectionManager` to publish `presence_update` frames on subscribe/unsubscribe and to memo disconnect timestamps (`packages/cluster/doorae/ws/manager.py`). `set_presence_service` keeps the coupling optional and breaks the cycle with presence.
- Added `PresenceUpdateOut` to the `OutgoingFrame` union (`packages/cluster/doorae/ws/protocol.py`).
- Extended `ParticipantOut` with `online`/`last_seen_at` and made `GET /rooms/{id}` call `presence.room_snapshot` (`packages/cluster/doorae/rooms/router.py`).
- Wired the service into the app lifespan (`packages/cluster/doorae/app.py:255-261`).
- `execute_room_query` (`packages/agent/doorae_agent/integrations/room_query.py`) now splits participants into `agent_candidates` (all non-self agents) and `online_agents` (only those with `online=True`, defaulted to True for legacy servers). `expected_count` uses the latter. `_deliver_result` adds a `미응답:` section listing offline agents as `(offline, N분 전)` and online no-shows as `(응답 없음)`. A new `_fmt_ago` helper formats ISO timestamps.
- Frontend: new `useParticipantPresence` hook (`src/hooks/useParticipantPresence.ts`) merges the REST seed with WS `presence_update` frames routed through a `doorae:presence:update` window event. `useWebSocket` emits that event. New `<PresenceDot>` (`src/components/PresenceDot.tsx`) renders a 6px dot with an accessible tooltip. `ParticipantListPopover` shows the dot per row; `RoomHeader` surfaces `agents n/N` and tags offline entries in the representative dropdown. `ChatPage` threads the presence map through all three.
- Tests: `test_presence_service.py` (7 tests covering three-tier resolution, batch snapshot, and publish), `test_ws_handler.py::TestPresenceBroadcast` (subscribe/unsubscribe broadcast), `test_rooms.py::test_get_room_detail_exposes_presence_fields`, `test_room_query.py::test_expected_count_excludes_offline` and `::test_missing_responder_label_offline`, `useParticipantPresence.test.ts` (merge semantics + identity-equality).

## Result

- Single source of truth: REST, WS, cross-room query, and UI all read through `PresenceService`.
- Cross-room query with a dead agent now reports `(1/1 응답)` and includes `미응답:\n- agent-name (offline, 마지막 응답 N분 전)` in the body — users see both the corrected count and the reason.
- 362 cluster tests pass (included 7 new), 108 agent tests pass (included 2 new + coexists with `#55`'s 20-test suite), 49 frontend vitest tests pass (including 4 new for merge semantics), `npm run build` clean.
- `#55` coordination: only touched the agent-participants filter plus `_deliver_result`'s missing-responder labels in `room_query.py` — left `_deliver_result`'s structure, metadata shape, and `RoomQuery` dataclass fields to `#55`. Rebase conflicts in `room_query.py` and `tsconfig.tsbuildinfo` resolved manually (tsbuildinfo took upstream; room_query merged by hand).

# feat(ui): thread agent engine through ParticipantOut so non-admins see engine badge (#102)

- Commit: `b200a87` (b200a873a497abd75e0206b19f72a704bdc6febf)
- Author: Changyong Um
- Date: 2026-04-18T16:25:06+09:00
- PR: #102

## Situation

#97 introduced `EntityAvatar` with an optional corner badge that renders
the backing agent's engine mark (Claude / Codex / Gemini / OpenHands /
…). The engine value was only reachable through the admin-gated
`useAgents()` hook, so only admin users saw the badge in the Sidebar
`AgentDMListAdmin` block. `MessageBubble`, `RoomHeader` (DM), and the
non-admin DM list all drew avatars without the engine glyph — losing
the "which engine is this?" affordance for guests and regular users.

## Task

- Expose `Agent.engine` on `ParticipantOut` so every client receives
  it through the single `GET /rooms/{id}` payload.
- Thread the value through the frontend: `Participant.engine?`,
  `ChatPage.dmAgent`, and the avatar props in `MessageBubble`.
- Keep user / guest rows at `engine=None` so `EntityAvatar` continues
  to skip the overlay for non-agents.
- Don't regress existing behaviour in the rooms router, pyd schema,
  or bubble render tests.
- Leave Sidebar non-admin DM rows untouched (out of scope — see
  Decisions).

## Action

- `packages/cluster/doorae/rooms/router.py:95-105` — added
  `engine: Optional[str] = None` to `ParticipantOut` with a doc
  comment referencing #102 and explaining the admin-gating rationale.
- `packages/cluster/doorae/rooms/router.py:278-305` — declared
  `engine: Optional[str] = None` in the participant loop, captured
  `agent.engine` alongside `agent.name` in the agent branch, and
  forwarded it through the `ParticipantOut(...)` constructor. The
  existing per-row `select(Agent)` is reused — no extra DB round
  trip.
- `packages/cluster/tests/test_rooms.py:264-309` — new
  `test_get_room_detail_exposes_agent_engine` case. Seeds an
  `Agent(engine="claude-code")`, attaches it as a Participant,
  hits `GET /rooms/{room.id}`, and asserts `engine="claude-code"`
  on the agent row and `engine is None` on the owner user row.
- `packages/cluster/frontend/src/pages/ChatPage.tsx:43-47` — added
  `engine?: string` to `Participant` with the #102 comment.
- `packages/cluster/frontend/src/pages/ChatPage.tsx` — `dmAgent`
  derivation now returns `{ id, name, engine }`, so `RoomHeader`'s
  `dmAgent` prop carries the engine through for DM rooms.
- `packages/cluster/frontend/src/components/MessageBubble.tsx:140-149`
  — forwards `engine={participant?.engine}` to `EntityAvatar`.
  `EntityAvatar` already guards by `kind==='agent'`, so this is safe
  for user/guest rows.
- `packages/cluster/frontend/src/components/MessageBubble.test.tsx` —
  upgraded the EntityAvatar mock to surface `data-engine`, added
  `engine: 'claude-code'` / `'codex'` to the agent fixtures, and
  wrote two new cases: one asserts `data-engine="claude-code"` on an
  agent sender, the other asserts the attribute is empty for a user
  sender. Existing 12 cases still pass.

## Decisions

Drawn from `.tmp/plan-102-participant-engine.md` §3.2:

- **Server payload vs. client-side join.** Weighed three options:
  extend `ParticipantOut` (chosen); have the frontend join
  `participants` with `useAgents()` results client-side; add a
  dedicated `/api/v1/rooms/{id}/agents` endpoint. Both alternatives
  fail the core goal because `useAgents()` is admin-gated and a new
  endpoint multiplies round-trips with no payload benefit. Extending
  the canonical single-fetch payload is the smallest change that
  reaches every viewer category.
- **Optional vs. required engine.** Chose `Optional[str] = None` over
  `""` (empty string) or splitting `ParticipantOut` into a
  discriminated agent/user union. Empty strings erase the signal
  "no engine because this is a user" and invite false negatives on
  the client; a discriminated union would have forced churn in every
  other `ParticipantOut` consumer (WS handler, tests, router) with no
  UX gain.
- **Scope boundary for Sidebar.** Non-admin DM rows in the sidebar
  rely on `agentDMs` from `GET /api/v1/rooms?is_dm=true`, which
  returns `RoomOut` (no participants, no representative agent
  details). Threading engine there would require either extending
  `RoomOut` or a new join endpoint — a schema decision worth its own
  PR. This commit deliberately limits scope to bubbles + DM header.
- **N+1 query preservation.** The participant loop already issues one
  `select(Agent)` per agent row. Reading `agent.engine` from the
  existing result adds zero new queries, so the optimization work
  (moving to `selectinload(...).options(joinedload(...))`) is
  orthogonal and left as a follow-up.
- **Assumption**: `Agent.engine` remains `Mapped[str]` (non-nullable).
  If a migration makes it nullable, `ParticipantOut.engine` stays
  safe (already `Optional`) but the frontend loses the glyph for
  those agents. Revisit if the Agent schema changes.
- **Assumption**: DM rooms contain exactly one agent participant;
  `ChatPage.dmAgent` uses `Object.values(participants).find(kind ===
  'agent')`. Multi-agent DMs would need to switch to
  `representative_agent_id` lookup.

## Result

- Agent participants in every `GET /rooms/{id}` response now carry an
  `engine` string; users/guests carry `engine: null`. Guests and
  regular users see the engine-mark badge in `MessageBubble` and on
  the `RoomHeader` DM avatar without gaining any new API permission.
- Tests: backend 404/404 green (`uv run pytest`, includes +1 new
  engine case in `test_rooms.py`); frontend 159/159 green
  (`npm run test`, includes +2 new MessageBubble engine cases);
  `npm run build` (tsc + vite) green.
- Still pending:
  - Sidebar non-admin DM rows show avatar with no engine badge
    (requires extending `agentDMs` payload — separate issue).
  - N+1 select per participant in the rooms router is unchanged
    (optimization deferred).
  - When `Participant` later gains additional fields (e.g. avatar
    image), the same single-fetch threading pattern applies.

# fix(agent,cluster): sync runtime-room-add with agent lifecycle (#227)

- Commit: `0c37695` (0c37695a1d3cab1b074abcb16641794ded85b6ea)
- Author: Changyong Um
- Date: 2026-04-21T22:45:43+09:00
- PR: #227

## Situation

Admins added a new room to an already-running agent via either
``POST /api/v1/rooms/{id}/participants`` or ``POST /api/v1/agents/{id}/rooms``
and the agent stayed silently offline in that room. The agent SDK
only auto-subscribes to a room on receipt of a ``JoinRoomOut`` WS
frame; the server's ``ConnectionManager.send_to`` call no-ops when
the target pid has no active subscription, so the frame dropped on
the floor and the machine-side ``--room`` argv was never refreshed.
The 2026-04-21 playwright session reproduced the bug: agent visible
in the first room, invisible in the room added at runtime, and a
process restart was the only recovery. The underlying architectural
gap was that neither add-path hit the machine bus — the declarative
``sync_desired_state`` contract was bypassed entirely for runtime
membership mutations.

## Task

- Make runtime-room-add a first-class lifecycle event so the machine
  receives an authoritative spawn frame, not a best-effort WS push.
- Preserve the 2026-04-12 "서브에이전트1/2" fix: adding a room to a
  ``pending`` agent must still trigger ``request_start``.
- Unify the two add-paths onto ``ensure_agent_in_room`` so the
  JoinRoomOut fan-out, idempotency, and membership invariants (#50)
  stay in one place.
- Add observability for the silent-drop failure mode so the same
  class of regression trips a metric rather than hiding until a user
  reports it.
- Do not touch the agent SDK reconnect logic or add a polling/queue
  recovery layer — those are separate follow-ups.

## Action

- ``packages/cluster/doorae/scheduler/lifecycle.py``: new
  ``AgentLifecycle.on_room_added(agent_id)`` helper. Looks up the
  agent's ``actual_state`` and dispatches: ``request_start`` for
  ``idle`` / ``stopped`` / ``crashed`` / ``pending``,
  ``bump_generation`` for ``running`` / ``starting``, no-op (with
  structlog) for anything else (stopping, missing agent).
  ``bump_generation`` already re-sends ``sync_desired_state`` with a
  fresh ``rooms`` list, so the machine's converger re-spawns with
  the new ``--room`` set automatically.
- ``packages/cluster/doorae/rooms/membership.py``: before the
  ``send_to`` fan-out inside ``ensure_agent_in_room``, fetch
  ``manager.connected_participant_ids()`` and for each ``other_pid``
  not in the set, increment
  ``agent_joinroom_drop_total{reason="not_subscribed"}`` + emit a
  structlog warning carrying ``agent_id`` / ``room_id`` /
  ``dropped_pid``. The ``send_to`` itself still runs for every pid
  (cheap no-op when unsubscribed, belt-and-braces for race windows).
  Falls back to "nothing connected" when the manager lacks the
  method — keeps legacy test doubles working.
- ``packages/cluster/doorae/rooms/router.py``: ``add_participant``
  now calls ``request.app.state.agent_lifecycle.on_room_added`` after
  ``ensure_agent_in_room`` on the agent branch. User branch
  unchanged — the helper is agent-specific.
- ``packages/cluster/doorae/api/v1/agents.py``: ``add_agent_room``
  refactored to use ``ensure_agent_in_room`` (replacing the direct
  ``Participant`` insert) and ``on_room_added`` (replacing the
  narrower ``idle/stopped/crashed/pending`` branch). The explicit
  409-on-duplicate check stays in front of ``ensure_agent_in_room``
  because the admin API contract requires a distinct error code
  whereas the helper is idempotent by design.
- ``packages/cluster/doorae/observability/metrics.py``: new
  ``agent_joinroom_drop_total`` Counter with a ``reason`` label
  (currently only ``"not_subscribed"``; label kept for future drop
  modes).
- Tests: ``test_membership.py`` — 2 new (drop counter bumps when no
  subscription, does not bump when every pid subscribed).
  ``test_lifecycle.py`` — 4 new (running→bump, pending→request_start,
  stopping→no-op, missing agent→no-op). ``test_rooms.py`` — 2 new
  (agent branch hits ``on_room_added``, user branch does not).
  ``test_agents_api.py`` — 1 new (running agent gets generation bump
  without state flip), existing
  ``test_add_room_redispatches_pending_agent`` unchanged.

## Decisions

**Recovery path — machine re-spawn vs WS push retry vs SDK polling**
(from ``.tmp/plan-227-runtime-room-add-lifecycle-sync.md`` §3).

- ``bump_generation`` chosen because it reuses an existing,
  production-exercised pattern (``skills``, ``mcp_templates``,
  ``agents.py:update`` all call it for running-agent config
  changes), and ``sync_desired_state`` is the authoritative contract
  between cluster and machine — runtime room changes should flow
  through the same pipe instead of a parallel WS-only channel.
- WS push retry with an offline-pid queue was rejected because it
  would require a stateful queue with TTL inside ``ConnectionManager``,
  would lose state across worker restarts, and solves a strictly
  smaller problem (the machine-side ``--room`` argv would still be
  stale after recovery — the agent process can't re-read argv).
- SDK polling (``GET /my-rooms`` on an interval) was rejected
  because 30–60 s lag is unacceptable for admin-initiated
  membership changes, and it does not match doorae's push-first
  architecture. Kept as a potential defense-in-depth follow-up.

**Trade-off accepted**: ``bump_generation`` on a running agent kills
an in-flight turn. This matches the existing behaviour for skill /
mcp / agent-update edits, so room adds now share the same
consistency model. If UX data surfaces real frustration from
dropped turns mid-conversation, a turn-boundary-aware variant is a
plausible follow-up — but inventing one preemptively would have
doubled the change size.

**Unifying through ``ensure_agent_in_room`` vs keeping the two paths
divergent**: unifying was chosen to close the #50-class invariant
(both add-paths now share the JoinRoomOut fan-out) at the cost of a
small refactor in ``agents.py``. The alternative — adding only the
``bump_generation`` call and leaving the direct ``Participant``
insert — would have left the endpoints asymmetric and repeated the
same mistake a future fifth path could fall into. The explicit
409-check compensates for ``ensure_agent_in_room``'s idempotent
semantics so admin-UI error handling doesn't regress.

**Drop observability in ``membership.py`` vs ``ws/manager.py``**:
placed in ``ensure_agent_in_room`` where the ``room_id`` /
``agent_id`` / ``other_pids`` context is available in a single
frame. Instrumenting ``send_to`` would have mixed JoinRoomOut drops
with unrelated frame drops (RoomMembershipChanged, per-message
fans, ...) and degraded the signal.

**Assumptions that, if violated, should trigger revisiting**:
(a) ``bump_generation`` actually causes the machine daemon to
re-spawn with the new rooms list — if the daemon learns to
hot-reconcile ``--room`` without killing the process, this entire
approach could be simplified to a generation bump without restart.
(b) ``sync_desired_state`` delivery is reliable enough that no
machine-side ack is needed — if machines start silently missing
frames, the follow-up would be adding frame-level acks to
``MachineBus``.

## Result

- 695 cluster tests pass (9 new). ``test_membership.py``,
  ``test_lifecycle.py``, ``test_rooms.py``, ``test_agents_api.py``
  all green. Agent / machine packages run clean in isolation
  (``packages/machine``: 283 pass, ``packages/agent``: 260 pass +
  1 pre-existing ``test_integrate_registers_handler`` failure
  caused by missing ``OPENAI_API_KEY`` in env — same failure on
  ``main``).
- User-visible effect: admin adds a running agent to a new room →
  machine receives ``sync_desired_state`` with refreshed ``rooms``
  list → agent process re-spawns with the correct ``--room`` argv
  → agent presence + message delivery work in the new room without
  requiring a manual restart.
- New operational signal:
  ``doorae_agent_joinroom_drop_total{reason="not_subscribed"}`` +
  ``membership.joinroom_dropped`` structlog event. Useful for
  catching the same class of regression early. Expected to be >0 at
  low volume (server-restart windows where ``_by_participant`` is
  briefly empty); a sustained rate would indicate a deeper
  subscription-tracking bug.
- Out of scope (deferred): (a) SDK-side ``GET /my-rooms`` polling
  for defense-in-depth, (b) offline-pid queue for ``send_to``,
  (c) turn-boundary-aware generation bump that would avoid killing
  in-flight responses. Rationale for each in the plan doc at
  ``.tmp/plan-227-runtime-room-add-lifecycle-sync.md``.

"""Prometheus metrics for the Anygarden chat server."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# ── #425 Phase 1 — turn-level observability from LifecycleFrames ──────
#
# These are fed from the single point where the cluster receives agent
# LifecycleFrames (ws/handler.py), independent of OTEL being enabled.
# Labels are deliberately BOUNDED: outcome (5 values) and engine (a
# handful). agent_id / room_id are intentionally NOT labels — that
# per-id breakdown lives in ActivityLog / OTEL, never in metric series
# (it would blow up cardinality).

# One increment per finished turn, labelled by terminal outcome. The
# ``engine`` is NOT on the handler_finished frame (it lives on the
# engine_call_* frames), so this counter is outcome-only; the
# per-engine latency breakdown is the histogram below.
agent_turns_total = Counter(
    "anygarden_agent_turns_total",
    "Total number of finished agent turns, by terminal outcome",
    ["outcome"],  # ok | failed | timeout | cancelled | rejected
)

# Engine-call wall time as measured by the agent (frame.duration_ms),
# observed when an engine_call_finished frame arrives (which carries
# both engine and outcome). Buckets span sub-second to the 15-min
# engine timeout.
engine_call_duration_ms = Histogram(
    "anygarden_engine_call_duration_ms",
    "Agent engine-call duration in milliseconds",
    ["engine", "outcome"],
    buckets=(100, 500, 1000, 5000, 15000, 60000, 300000),
)

# #427 Phase 2 — turns that started but never produced a terminal event
# and were swept to ``handler_orphaned`` by the cluster. A started-but-
# never-finished failure mode that is otherwise only visible in
# ActivityLog; surfacing it as a counter makes it alertable.
agent_turns_orphaned_total = Counter(
    "anygarden_agent_turns_orphaned_total",
    "Total number of agent turns promoted to handler_orphaned by the sweeper",
)

# #447 Wave 1a — agents flipped running→crashed by the heartbeat reaper
# because their last_heartbeat_at went stale AND their placed machine
# stopped being online (e.g. power loss). Without this sweep those rows
# linger as ``running`` forever and pollute bin-pack placement; the
# counter makes the reap rate alertable.
agents_crashed_by_sweep_total = Counter(
    "anygarden_agents_crashed_by_sweep_total",
    "Total number of agents flipped to crashed by the stale-heartbeat sweeper",
)

# ── #455 Wave 2a — token-budget active-stop / incidents ──────────────
#
# ``evaluate_cost_event`` records an incident whenever a successful usage
# row pushes a scope's observed-token SUM over a soft (warn) or hard
# (ceiling) threshold, and — for AGENT-scope hard breaches only —
# actively stops the agent. These counters make the breach + stop rate
# alertable. Like the other reliability counters, scope ids are NOT
# labels (cardinality); only the bounded ``threshold`` dimension is.
budget_incidents_total = Counter(
    "anygarden_budget_incidents_total",
    "Total number of token-budget incidents recorded (new open rows)",
    ["threshold"],  # "soft" | "hard"
)

# Agents actively stopped by the budget active-stop path (AGENT-scope
# hard breach → request_stop + pause_reason='budget'). ROOM / GLOBAL
# breaches are incident-only and never increment this.
agents_stopped_by_budget_total = Counter(
    "anygarden_agents_stopped_by_budget_total",
    "Total number of agents stopped by the token-budget active-stop path",
)

ws_connections_active = Gauge(
    "anygarden_ws_connections_active",
    "Number of currently active WebSocket connections",
)

ws_messages_total = Counter(
    "anygarden_ws_messages_total",
    "Total number of WebSocket messages processed",
    ["direction"],  # "inbound" | "outbound"
)

ws_errors_total = Counter(
    "anygarden_ws_errors_total",
    "Total number of WebSocket errors",
)

http_requests_total = Counter(
    "anygarden_http_requests_total",
    "Total number of HTTP requests",
    ["method", "path", "status"],
)

db_queries_total = Counter(
    "anygarden_db_queries_total",
    "Total number of database queries executed",
    ["operation"],  # "select" | "insert" | "update" | "delete"
)

# ── Machine & Agent scheduling metrics ───────────────────────────────

machines_online = Gauge(
    "anygarden_machines_online",
    "Number of currently online machines",
)

agents_by_state = Gauge(
    "anygarden_agents_by_state",
    "Number of agents grouped by lifecycle state",
    ["state"],  # "pending" | "starting" | "running" | "crashed" | "stopping" | "stopped"
)

# ── Anonymous-guest metrics (§11 design doc) ─────────────────────────

# Currently-connected guests. Incremented on WS subscribe, decremented
# on unsubscribe — same lifecycle as ``ws_connections_active`` but
# filtered to guest sessions.
guest_active = Gauge(
    "anygarden_guest_active",
    "Number of currently connected anonymous-guest WebSocket sessions",
)

# Room invite creation and redemption counters. ``invites_created``
# lets operators watch admin issuing behaviour; ``invites_used``
# captures how often guests actually accept. The two together let us
# catch "tons of invites, few accepts" (likely spam) and "few
# invites, many accepts" (token leak).
invites_created_total = Counter(
    "anygarden_invites_created_total",
    "Total number of room invite links issued",
)
invites_used_total = Counter(
    "anygarden_invites_used_total",
    "Total number of room invite redemptions (POST /auth/guest)",
)

# Guest-specific rate-limit trip counter keyed by which layer tripped.
# ``cooldown`` = stricter per-guest token bucket.
# ``room_aggregate`` = §11.7 room-wide cap across all guests.
guest_rate_limited_total = Counter(
    "anygarden_guest_rate_limited_total",
    "Total number of guest WS sends rejected by a rate-limit layer",
    ["scope"],
)

# ── #227 — JoinRoomOut delivery telemetry ────────────────────────────
#
# ``ensure_agent_in_room`` fans a ``JoinRoomOut`` frame to every *other*
# participant row belonging to the same agent so the agent SDK can
# auto-subscribe to the new room over its existing WS sessions. Those
# sends are best-effort: when a target pid has no active subscription
# (``ConnectionManager._by_participant`` miss) the frame is silently
# dropped. Before #227 that silent drop was the failure mode — the
# agent stayed offline in the new room until process restart. The
# counter below is the "did we just drop?" signal so the same class of
# regression trips an alert instead of hiding. The ``reason`` label is
# forward-looking in case future drop modes (serialization error, ws
# send failure) need separate attribution.
agent_joinroom_drop_total = Counter(
    "anygarden_agent_joinroom_drop_total",
    "Total number of JoinRoomOut frames dropped because the target "
    "participant had no active WebSocket subscription",
    ["reason"],  # "not_subscribed"
)

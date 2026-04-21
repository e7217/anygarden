"""Prometheus metrics for the Doorae chat server."""

from __future__ import annotations

from prometheus_client import Counter, Gauge

ws_connections_active = Gauge(
    "doorae_ws_connections_active",
    "Number of currently active WebSocket connections",
)

ws_messages_total = Counter(
    "doorae_ws_messages_total",
    "Total number of WebSocket messages processed",
    ["direction"],  # "inbound" | "outbound"
)

ws_errors_total = Counter(
    "doorae_ws_errors_total",
    "Total number of WebSocket errors",
)

http_requests_total = Counter(
    "doorae_http_requests_total",
    "Total number of HTTP requests",
    ["method", "path", "status"],
)

db_queries_total = Counter(
    "doorae_db_queries_total",
    "Total number of database queries executed",
    ["operation"],  # "select" | "insert" | "update" | "delete"
)

# ── Machine & Agent scheduling metrics ───────────────────────────────

machines_online = Gauge(
    "doorae_machines_online",
    "Number of currently online machines",
)

agents_by_state = Gauge(
    "doorae_agents_by_state",
    "Number of agents grouped by lifecycle state",
    ["state"],  # "pending" | "starting" | "running" | "crashed" | "stopping" | "stopped"
)

# ── Anonymous-guest metrics (§11 design doc) ─────────────────────────

# Currently-connected guests. Incremented on WS subscribe, decremented
# on unsubscribe — same lifecycle as ``ws_connections_active`` but
# filtered to guest sessions.
guest_active = Gauge(
    "doorae_guest_active",
    "Number of currently connected anonymous-guest WebSocket sessions",
)

# Room invite creation and redemption counters. ``invites_created``
# lets operators watch admin issuing behaviour; ``invites_used``
# captures how often guests actually accept. The two together let us
# catch "tons of invites, few accepts" (likely spam) and "few
# invites, many accepts" (token leak).
invites_created_total = Counter(
    "doorae_invites_created_total",
    "Total number of room invite links issued",
)
invites_used_total = Counter(
    "doorae_invites_used_total",
    "Total number of room invite redemptions (POST /auth/guest)",
)

# Guest-specific rate-limit trip counter keyed by which layer tripped.
# ``cooldown`` = stricter per-guest token bucket.
# ``room_aggregate`` = §11.7 room-wide cap across all guests.
guest_rate_limited_total = Counter(
    "doorae_guest_rate_limited_total",
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
    "doorae_agent_joinroom_drop_total",
    "Total number of JoinRoomOut frames dropped because the target "
    "participant had no active WebSocket subscription",
    ["reason"],  # "not_subscribed"
)

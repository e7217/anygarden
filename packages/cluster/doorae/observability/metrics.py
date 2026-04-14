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

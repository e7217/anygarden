# fix(rooms): unify REST metadata field name + prevent duplicate room_query forward (#61)

- Commit: `2ccd78d` (2ccd78d62261784bd3408423a6fd653388dd88e9)
- Author: Changyong Um
- Date: 2026-04-16T11:48:20+09:00
- PR: #61

## Situation

Two independent bugs were breaking the `#room` query UX added in #55/#42. First, the REST history endpoint (`GET /api/v1/rooms/{id}/messages`) returned the raw DB column name `extra_metadata`, while the WebSocket path and the frontend both use `metadata` — so a page refresh silently demoted `room_query` / `room_query_forward` cards to plain chat bubbles. Second, when a user mentioned `<#room:target>` from a source room containing more than one agent, the server attached `room_query` metadata to the broadcast and *every* agent that saw it forwarded `[ROOM_QUERY]` to the target room, multiplying the fan-out.

## Task

- Make REST `MessageOut` serialize `extra_metadata` as `metadata` without renaming the SQLAlchemy column (the column name is baked into the ORM mapping and a migration would be disruptive).
- Identify the representative agent at broadcast time and let only that agent forward the query.
- Keep the transition safe: legacy servers (no `representative_agent_id`) and legacy clients (no `_agent_id`) must continue to work during rolling deploys.
- Cover both paths with unit + WS integration tests.

## Action

- `packages/cluster/doorae/messages/router.py` — added `Field(serialization_alias="metadata")` to `MessageOut.extra_metadata` and `populate_by_name=True` to `model_config` so JSON responses expose the field as `metadata` while the attribute keeps mapping to the ORM column.
- `packages/cluster/doorae/ws/protocol.py` — added optional `agent_id: Optional[str] = None` to `WelcomeOut`.
- `packages/cluster/doorae/ws/handler.py` — welcome frame now sets `agent_id=identity.id if identity.kind == "agent" else None`; `room_query` metadata dict gained `representative_agent_id: rep_agent_id` alongside the existing `target_room_id` / `query_id` / `source_participant_id` fields.
- `packages/agent/doorae_agent/client.py` — `ChatClient.__init__` gained `self._agent_id: str | None = None`; the welcome-frame handler stores `data.get("agent_id")` when present (without clobbering on later reconnects).
- `packages/agent/doorae_agent/integrations/base.py` — `should_respond()` rule 2b now extracts `representative_agent_id` from `room_query` and compares it to `client._agent_id`. Match → `True`, mismatch → `False`. If either side is missing, falls back to the previous `True` for backward compatibility.
- Tests: new `packages/cluster/tests/test_messages.py` for the REST alias (present/absent metadata); new `TestWelcomeAgentId` class in `test_ws_handler.py` covering user (`agent_id is None`) and agent (`agent_id == agent.id`) welcomes; existing `test_room_mention_attaches_room_query` asserts the new `representative_agent_id` field; `test_client.py` gained `TestChatClientWelcomeParsing` (3 cases) and `test_should_respond.py` gained three representative-gate cases (match / mismatch / legacy-no-`_agent_id`).

## Result

REST history now serves `metadata` so the frontend's card renderers survive refresh. In a multi-agent source room, only the representative forwards `[ROOM_QUERY]` to the target room, eliminating the N-duplicate fan-out. Legacy fallbacks keep the system working during rolling deploys. Full test suites: 366 passing in cluster, 117 in agent, 209 in machine; frontend `tsc -b && vite build` clean.

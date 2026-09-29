# chore(rooms): remove unused wake_triggers setting (#740)

- Commit: `1257db6` (1257db6c6371a1e945a76790ef6e65c76ae25a0e)
- Author: Changyong Um
- Date: 2026-09-29T13:59:43+09:00
- PR: #740

## Situation

D-1 (#624, landed in #633) added a per-room `rooms.wake_triggers` policy (`message` / `mention` / `reminder`) and a server-side `metadata.wake_trigger` stamp that agents' `decide_policy` treated as an explicit wake. In practice almost none of it was used: `mention` was never checked anywhere, `reminder` was only read by `broadcast_reminder_wake()`, which only tests called, and `message` was only stamped on REST sends. No settings UI could change the value, so every room kept the default `["mention", "reminder"]`. The `message`/`reminder` stamps also bypassed the agent's normal judgment chain, which conflicts with the mention-only policy being introduced in #739.

## Task

- Remove the column, the API field, the REST stamp, the reminder emitter, and the agent-side short-circuit.
- Keep the emoji reactions feature (`message_reactions`) from the same PR.
- Keep the diff in `messages/router.py` and `integrations/base.py` minimal because #739 edits nearby lines and this branch is rebased on it later.

## Action

- `packages/cluster/anygarden/db/migrations/versions/079_drop_room_wake_triggers.py` (new): drops `rooms.wake_triggers` with `batch_alter_table`. The downgrade restores the 069 definition (`JSON NOT NULL`, server default `["mention", "reminder"]`).
- `packages/cluster/anygarden/db/models.py`: removed `Room.wake_triggers`.
- `packages/cluster/anygarden/rooms/router.py`: removed `RoomUpdate.wake_triggers`, `_VALID_WAKE_TRIGGERS`, `_DEFAULT_WAKE_TRIGGERS`, and the validation and assignment in the PATCH handler.
- `packages/cluster/anygarden/messages/router.py`: removed the `room_wake_triggers` lookup and the D-1 stamp block.
- `packages/cluster/anygarden/messages/service.py`: removed `broadcast_reminder_wake`.
- `packages/agent/anygarden_agent/integrations/base.py`: removed the D-1 `wake_trigger` branch from `decide_policy`.
- Tests:
  - Removed the reminder-wake test from `test_message_reactions.py` and kept the reaction tests.
  - Replaced `TestWakeTriggerStampD1` with a legacy-stamp test: an unmentioned agent message still carrying `wake_trigger: message|reminder` stays SKIP.
  - Removed the stale `wake_trigger` fixture keys from `test_room_execution.py` and `test_room_cursor.py`.
  - Moved the migration head assertions in `test_migrations.py` and `test_federation_trust.py` to 079.

## Decisions

- Options weighed:
  - Remove the code but keep the column: no migration risk, but the model and schema drift apart and a dead column remains.
  - Remove the code and drop the column: needs one migration, done with the same SQLite `batch_alter_table` pattern 069 used to add it. **Chosen.**
  - Build a UI and keep the feature: conflicts with the #739 mention policy and has no use case (AGENTS.md: separate "possible to implement" from "needs to be exposed").
- What decided it: every room in the local DB holds the default value and no UI could change it, so there was no data to keep.
- `RoomUpdate` uses pydantic's default config (extra fields ignored). A client that still sends `wake_triggers` gets a normal response and the value is ignored. We chose this over a 422 to avoid breaking stale clients.
- Agents ignore a legacy stamp from an older server, and the normal chain decides.
- Assumption: live node databases also hold only the default value. The server migrates itself on start (`_ensure_schema_ready`). Revisit if a deployment is found that customised `wake_triggers`.

## Result

- `wake_trigger` no longer appears in runtime code. It remains only in migrations 069/079, revision IDs, and the legacy-stamp test.
- Migration round-trip on a scratch SQLite DB (upgrade head, downgrade -1, upgrade head) succeeded, and the downgrade restored the original column definition.
- Agent suite: 659 passed. Targeted cluster tests (reactions, migrations, federation trust): 97 passed. Full cluster suite results are in the PR.
- Pending: rebase after #739 merges.

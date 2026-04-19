# feat(cluster,agent): speaker-strategy schema + welcome propagation (#159 Phase A)

- Commit: `b90a4b6`
- Author: Changyong Um
- Date: 2026-04-19
- PR: pending
- Issue: #159 (umbrella)

## Situation

#157's safeguards shipped, so inter-agent loops have brake pedals. #159 now opens the accelerator: let rooms pick how turns get handed out (mentioned-only, round-robin, orchestrator). Phase B/C will wire the `decide_policy` branches and the `handoff_to` tool, but both need somewhere to read the strategy from — schema, welcome frame, client cache. Phase A is that foundation.

## Task

- Add four nullable-safe columns to `rooms` (`speaker_strategy`, `orchestrator_agent_id`, `next_speaker_participant_id`, `current_speaker_index`) with server defaults that preserve current behaviour.
- Propagate the three that agents need (`speaker_strategy`, `orchestrator_agent_id`, `next_speaker_participant_id`) through `WelcomeOut` so `decide_policy` can dispatch without a round-trip.
- Cache per-room on the SDK side; default `mentioned_only` when the server omits the fields so older servers keep working.
- Don't break any existing cluster / agent regression.

## Action

- `packages/cluster/doorae/db/migrations/versions/024_room_speaker_strategy.py` (new) — adds the four columns via `batch_alter_table`. FK constraints are **named** (`fk_rooms_orchestrator_agent_id`, `fk_rooms_next_speaker_participant_id`) so SQLite downgrade via the batch helper doesn't explode with "Constraint must have a name".
- `packages/cluster/doorae/db/models.py`
  - `Room` grows `speaker_strategy`, `orchestrator_agent_id`, `next_speaker_participant_id`, `current_speaker_index` (plus server defaults matching the migration).
  - `Room.participants` and `Participant.room` now declare `foreign_keys=` explicitly — adding a second rooms↔participants FK made SQLAlchemy unable to infer the join condition. Failure mode was `AmbiguousForeignKeysError` hitting roughly 200 cluster tests the moment the schema loaded.
- `packages/cluster/doorae/ws/protocol.py` — `WelcomeOut` gains three optional fields with compatible defaults (`speaker_strategy="mentioned_only"`, `orchestrator_agent_id=None`, `next_speaker_participant_id=None`).
- `packages/cluster/doorae/ws/handler.py` — one extra `select` right before `WelcomeOut(...)` reads the room row and populates the new fields. Defaults kick in when the row is missing (e.g. admin removed the room mid-session). Runs in a fresh session from the opt-out read path to keep failure domains separate.
- `packages/agent/doorae_agent/client.py`
  - `__init__` initialises three per-room dicts (`_speaker_strategy`, `_orchestrator_agent_id`, `_next_speaker_participant_id`).
  - The `welcome` branch of `_process_frame` caches whatever the server sent; absent fields fall back to `"mentioned_only"` / `None` so the pre-#159 code path survives unchanged.
- `packages/cluster/tests/test_migrations.py` — bumped the hard-coded head version sentinel from `"023"` to `"024"` (five occurrences). The assertions were guarding "fresh bootstrap stamps the latest version"; no semantic change.
- `packages/agent/tests/test_speaker_strategy_welcome.py` (new, 5 tests): empty-on-construction, default-when-fields-absent, explicit-propagation, per-room isolation, welcome-refreshes-cache.

## Result

- `packages/cluster/` — **600 passed**, no regressions (587 old + 13 from #157 C).
- `packages/agent/` — **194 passed** (189 old + 5 new) + 1 pre-existing `OPENAI_API_KEY` flake.
- Migration smoke tests: fresh bootstrap stamps 024, upgrade from 023 → 024 preserves rows, downgrade 024 → 023 clean under SQLite batch mode.
- Schema + welcome are live. Phase B can now read `_speaker_strategy[room_id]` and branch `decide_policy` without further plumbing. Phase C's `handoff_to` tool will write `Room.next_speaker_participant_id` and expect the welcome to re-emit it on the next connect.

# feat(agent): durable room cursors with a stale catch-up guard

- Commit: `d6d8343`
- Author: Changyong Um
- Date: 2026-09-21
- PR: —

## Situation

`ChatClient` tracked the last seen `seq` per room in memory only. The server replays missed messages on reconnect, but only when the connection carries `since_seq > 0` (`ws/handler.py`, paged at 200 with a 10000 ceiling), and a respawned agent always started from `{}`. So everything that arrived while an agent was down was never delivered. This was observed live on 2026-09-19: a mention sent to a stopped agent (`L1-mention` seq 189) never reached it after restart, and the request had to be re-sent by hand.

## Task

- Make the room cursor survive a respawn so reconnect replays the gap.
- Do not let the replay of a long outage turn into a burst of stale replies.
- Leave the plain text client and the unit tests untouched — in particular, nothing may be written into the working directory during tests.

## Action

- `packages/agent/anygarden_agent/room_cursor_store.py` (new): `load_cursors` / `save_cursors` for a `room_id -> seq` map in `.anygarden-room-cursors.json`, atomic temp-file + replace, best-effort (missing or corrupt file degrades to `{}`). Non-int and non-positive values are dropped, and `bool` is rejected explicitly so a stray `true` cannot become a cursor of 1.
- `packages/agent/anygarden_agent/client.py`: new keyword-only `state_dir`; `_last_seq` is restored from it on construction, and a new `_note_seq()` advances the cursor monotonically and persists it. `_process_frame` calls `_note_seq` instead of updating the dict inline.
- `packages/agent/anygarden_agent/cli.py`: the agent process passes `state_dir=Path.cwd()` — its materialized agent dir.
- `packages/agent/anygarden_agent/integrations/base.py`: `decide_policy` rule 1b — `_is_stale_catchup()` returns `INGEST_ONLY` for frames older than `STALE_CATCHUP_SECONDS` (1h, `ANYGARDEN_AGENT_CATCHUP_MAX_AGE` overrides, `0` disables). Frames with no parsable `created_at` keep the previous behavior.
- `packages/agent/tests/test_room_cursor.py` (new): store round-trip/corruption/filtering, cursor survives a respawn, cursor never rewinds, nothing is written without `state_dir`, and the guard's four cases (recent mention responds, stale mention ingests, no timestamp responds, own stale message still skipped).

## Decisions

- The user chose this option over two alternatives: replay everything and answer it all, or add a server-side pending-wake queue with its own table and migration. Cursor + age guard needs no server change and no schema, and the server's replay path already exists.
- Persistence is opt-in through `state_dir` rather than implicitly `Path.cwd()`. The first attempt used the cwd directly (mirroring `engine_session_store`) and immediately broke 24 tests: the shared cwd leaked cursors between `ChatClient` instances and left `.anygarden-room-cursors.json` inside `packages/agent`. Making the caller name the directory keeps the durable behavior in the one place that wants it.
- The guard keys on message age instead of an explicit "this is a replay" flag, because no such flag exists on the wire and live traffic is never an hour old. If replayed frames ever gain a marker, this rule should switch to it.
- 1h is a guess at "still worth answering" and is env-tunable for that reason.
- The guard is placed right after the self-message skip, so a stale frame is absorbed as context before any mention/strategy rule can promote it to a reply.

## Result

- `cd packages/agent && uv run pytest`: 583 passed (15 new).
- Lint on the changed files reports only two pre-existing `UP037` findings on untouched lines.
- Not yet verified live against a real respawn; that needs the merged build running on the local node.

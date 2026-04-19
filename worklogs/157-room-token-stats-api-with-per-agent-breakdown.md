# feat(cluster): room token-stats API with per-agent breakdown (#157 Phase C)

- Commit: `013b0be`
- Author: Changyong Um
- Date: 2026-04-19
- PR: pending
- Issue: #157 (umbrella)

## Situation

Phases A (#160) and B (#161) added two active defenses — prefix abuse guard and semantic cycle detection — but gave operators no way to *see* how hot a room was running. The 2026-04-19 deep-research report singles out observability as the underrated layer in the 5-layer loop/budget model: without it, a room that's about to trip R1/R3 is indistinguishable from one that's genuinely quiet. The #159 Phase D UI that #157 is supposed to unblock also needs a per-agent slice, not just a room total.

## Task

- Expose a read-only admin endpoint that aggregates token estimates per rolling window (1h, 24h).
- Break the numbers out per participant so #159 Phase D can render the usage drawer without reaggregating client-side.
- Keep implementation conservative — no live budget enforcement this round, that's the conditional R7 follow-up.
- Preserve existing cluster + agent regression.

## Action

- `packages/cluster/doorae/rooms/token_stats.py` (new)
  - `estimate_tokens(content)` — `max(1, len // 4)`. Conservative enough for trend observation across engines.
  - `DEFAULT_WINDOWS = [("window_1h", 1h), ("window_24h", 24h)]` — explicit labels so the JSON key is stable (prevents a `timedelta(hours=24)` → `window_1d` collision).
  - `AgentUsage` + `WindowStats` frozen dataclasses; `get_room_token_stats(session, room_id, *, windows)` joins `Message` → `Participant` → `Agent` with outer joins so user messages (no Agent row) still aggregate as `agent_name=None`. Sorted by tokens desc for stable UI ordering.
  - `serialise_window` translates to the JSON response shape (`tokens`, `messages`, `agents`, `per_agent[]`).
- `packages/cluster/doorae/rooms/router.py`
  - New `GET /{room_id}/token-stats` handler with `get_admin_identity` dep. 404 when the room is absent. Delegates to `get_room_token_stats` + `serialise_window` per window and returns the dict.
- `packages/cluster/tests/test_room_token_stats.py` (new) — 13 tests:
  - 4 pure unit tests on `estimate_tokens` (empty, short, monotonic, quarter-length).
  - 7 HTTP integration tests: admin 200, regular 403, unknown room 404, 1h window totals (100+10+20=130 tokens / 3 messages / 3 agents), 24h window adds the 5h-old message (180 tokens / 4 messages), per_agent sorted and correctly joined to `agent_name`, >24h ancient messages excluded.
  - 2 direct-call tests on `get_room_token_stats` for empty-room zeros and response-shape round-trip through `serialise_window`.
- Fixture follows the `test_agents_api.py` convention (in-memory SQLite + `ASGITransport`) and pre-loads a room with 1 user + 2 agents + 5 messages spanning recent / older / ancient buckets.

## Result

- 13 new cluster tests pass.
- `uv run pytest packages/cluster/` — **600 passed** (up from 587), no regressions.
- `uv run pytest packages/agent/` — **189 passed** + 1 pre-existing `OPENAI_API_KEY` flake, no regressions.
- Admin UI (#159 Phase D) can consume the `per_agent` slice directly; conditional auto-cutoff (R7) can build on the same aggregation without touching the response shape.
- Closes out #157's active + observable layers. Phase D (manual drill) is not a code PR.

# fix(campaigns): check the room before the evaluator owner, share error mapping and align evaluator limits in the API (#930)

- Commit: `1fa6350` (1fa635079c55a691e1ec6744d28bee94058c52e4)
- Author: Changyong Um
- Date: 2026-10-26T16:08:26+09:00
- PR: #930

## Situation

The `create_campaign` API endpoint had two ordering and consistency problems. First, the room-eligibility checks (archived, no lead agent) were buried inside `create_campaign` in `service.py`, so the API layer could not distinguish them from evaluator policy errors—both surfaced as 422, even though an archived room is semantically a conflict (409). Second, the evaluator field constraints on `EvaluatorIn` were open-ended; there were no upper-bound limits that matched what the machine-side `CampaignEvalRequestFrame` could parse, making it possible to store an evaluator the engine would later refuse.

## Task

- Extract the room-eligibility checks from `create_campaign` into a standalone `check_campaign_room` service function so the API can call it before the evaluator-owner permission check and map errors independently.
- Add a shared `_policy_error` helper in the API that maps `ROOM_ARCHIVED` → 409 and all other `CampaignPolicyError` codes → 422.
- Align `EvaluatorIn` field limits with the constants already declared in `campaigns.policy` (`MAX_EVAL_COMMAND_CHARS`, `MAX_EVAL_PATH_CHARS`, `MAX_EVAL_PIN_PATHS`, `MAX_EVAL_TIMEOUT_SECONDS`, `MAX_SCORE_KEY_CHARS`).
- Introduce `CampaignAttemptNotFound` (a typed exception) in `actions.py` so `LookupError` (a broad built-in) no longer silently swallows unrelated `KeyError` bugs as 404 responses.
- Add tests covering: room without a lead reported before evaluator check, archived room → 409, oversized evaluator fields → 422, `_policy_error` unit test, and a regression guard ensuring a plain `KeyError` is not converted to 404.

## Action

**`packages/cluster/anygarden/campaigns/service.py`**
- Extracted the `if room.archived_at`, `if room.project_id is None`, and `find_agent_participant` checks from `create_campaign` into new `async def check_campaign_room(db, room) -> str`.
- `create_campaign` now calls `await check_campaign_room(db, room)` and uses its returned `agent_id`.
- Added `"check_campaign_room"` to `__all__` in alphabetical order.

**`packages/cluster/anygarden/api/v1/campaigns.py`**
- Imported `MAX_EVAL_COMMAND_CHARS`, `MAX_EVAL_PATH_CHARS`, `MAX_EVAL_PIN_PATHS`, `MAX_EVAL_TIMEOUT_SECONDS`, `MAX_SCORE_KEY_CHARS` from `campaigns.policy`.
- Added upper-bound `Field` constraints to `EvaluatorIn.command`, `.cwd`, `.timeout_sec`, `.pin_paths`, and `.score_key`.
- Added `_policy_error(exc)` helper: returns `_conflict` for `ROOM_ARCHIVED`, `_invalid` for everything else.
- In `create_campaign`, inserted an explicit `await check_campaign_room(db, room)` block (with `_policy_error` mapping) *before* the `_can_set_evaluator` check, so room rules are enforced independently of evaluator ownership.
- Changed the inline `if exc.code == "ROOM_ARCHIVED": ...` duplicate in `_run` and `create_campaign` to use `_policy_error`.
- Changed `except LookupError` → `except actions.CampaignAttemptNotFound` in `_run`.

**`packages/cluster/anygarden/campaigns/actions.py`**
- Added `class CampaignAttemptNotFound(Exception)`.
- Changed `_cycle_attempt` to raise `CampaignAttemptNotFound` instead of `LookupError`.

**`packages/cluster/tests/test_campaigns_api.py`**
- Added five new tests (see Task section above).

## Decisions

- **Extract `check_campaign_room` vs. inline check in API**: Keeping the logic in the service layer avoids duplicating the room-eligibility rules wherever a campaign is created (CLI, background tasks, etc.). The API layer only needs to call it early and map the error code. The alternative—duplicating the checks in the endpoint—was rejected because it would diverge from the service's invariants over time.
- **`_policy_error` shared helper vs. per-call inline**: The same `ROOM_ARCHIVED → 409` mapping was already duplicated in `_run` and would be duplicated again in `create_campaign`. A single helper eliminates the drift surface. Alternatives considered: a dict lookup table (rejected as over-engineered for two codes) or handling it at the service layer (rejected because HTTP status is an API concern).
- **`CampaignAttemptNotFound` vs. `LookupError`**: `LookupError` is the base of `KeyError` and `IndexError`; catching it in `_run` would silently convert any look-up bug (e.g. a missing dict key in a new code path) into a 404. A typed domain exception makes the catch precise. The only downside is a small import change.
- **Align `EvaluatorIn` limits at the API layer vs. in the DB model**: The limits already exist in `campaigns.policy` (used by the machine-side parser). Re-using those constants in the Pydantic schema makes the API refuse oversized payloads with a 422 before they reach the DB, without introducing a new source of truth.

## Result

- An archived room now returns 409 (conflict) immediately, before the evaluator-owner permission check, giving callers a clear signal.
- A room without a lead agent returns 422 with `reason: ROOM_LEAD_REQUIRED` before the evaluator check, which previously could confusingly fail later.
- `EvaluatorIn` now rejects oversized field values with 422 at input validation time.
- `CampaignAttemptNotFound` prevents unrelated `KeyError` bugs from masquerading as 404 responses.
- All new behaviours are covered by the five new API tests.

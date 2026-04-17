# fix(server): emit UTC-aware ISO datetimes so KST clients don't shift by 9h (#93)

- Commit: `dcee45e` (dcee45eba9ae56d67ac28450b83c9b8c1ef846fb)
- Author: Changyong Um
- Date: 2026-04-17T14:39:59+09:00
- PR: #93

## Situation

KST users reported that chat message timestamps were nine hours behind wall-clock and that freshly sent room-query questions never produced a pending chip in `RoomQueryBanner`. Investigation showed the server storing aware UTC datetimes via `_utcnow()` and `DateTime(timezone=True)`, but SQLite silently discards the `tzinfo` so values round-trip as naive. Pydantic v2 then serializes naive datetimes without a timezone designator (e.g. `"2026-04-17T05:12:03.456789"`), and ECMAScript parses such strings as *local* time. In KST(+9) that shifted every timestamp nine hours into the past, pushing every new pending entry past the 7-minute TTL filter in `pending-queries.ts` and miscolouring message bubbles.

## Task

- Make server-emitted datetimes always carry a timezone designator, across both WS frames and REST responses.
- Keep the fix compatible with Pydantic v2's default ISO serialization so model response classes don't each need a custom serializer.
- Cover the `messages_fts` virtual table whose timestamps live outside the ORM.
- Add a client-side defensive parser so older cached responses and possible rollbacks don't re-introduce the nine-hour shift.
- Preserve the existing pending-query TTL semantics (7 minutes, only applied to orphan pending entries) while fixing the parsing bug.

## Action

- Introduced `UtcDateTime(TypeDecorator)` in `packages/cluster/doorae/db/types.py` wrapping `DateTime(timezone=True)`. `process_bind_param` promotes naive inputs to UTC; `process_result_value` re-attaches `tzinfo=UTC` so downstream code (and Pydantic serialization) never sees a naive datetime.
- Replaced all 25 `DateTime(timezone=True)` usages in `packages/cluster/doorae/db/models.py` with `UtcDateTime` and dropped the now-unused `DateTime` import.
- Added `_fts_created_at_to_iso` in `packages/cluster/doorae/api/v1/search.py` to parse the FTS row's raw SQLite string (`"YYYY-MM-DD HH:MM:SS"`), promote to UTC, and re-emit as ISO 8601 with `+00:00`.
- Added `parseServerDate` in `packages/cluster/frontend/src/lib/datetime.ts`: treats designator-less ISO strings as UTC, passes through `Z` / `±HH:MM` / `±HHMM` unchanged. Applied in `pending-queries.ts:122` (TTL filter) and `MessageBubble.tsx:45` (`formatTime`).
- Regression tests:
  - `tests/test_db_types.py` — aware/naive/None round-trips and an invariant check that serialized output carries a designator.
  - `tests/test_search_fts_iso.py` — six shape variants of the FTS helper.
  - `tests/test_messages.py::TestRestMessageCreatedAtTimezone` — asserts every `GET /rooms/{id}/messages` response's `created_at` matches `/(Z|[+\-]\d{2}:?\d{2})$/`.
  - `frontend/src/lib/datetime.test.ts` — six parsing cases including `+0900` and invalid input.
  - `frontend/src/lib/pending-queries.test.ts` — two new cases asserting TZ-less ISO from a 1-minute-ago / 8-minute-ago UTC instant still pass/fail the TTL correctly in a KST-ish scenario.

## Decisions

Mined from `.tmp/plan-93-A-datetime-utc.md` (§3.2):

- **SQLAlchemy `TypeDecorator` vs. Pydantic `field_serializer` vs. SA `event.listen("load")` vs. client-only fix**: the TypeDecorator route was picked because (a) it's a single definition that applies to every model automatically, (b) new models importing `UtcDateTime` inherit the invariant without reviewer vigilance, and (c) it's the textbook SQLAlchemy idiom so future developers will recognize it. A per-model `field_serializer` would have required touching 16+ response models and risked silent drift whenever a new datetime field is added. `event.listen("load")` does roughly the same work less visibly. Client-only fix was explicitly rejected because external consumers (agent SDK, `doorae-machine`) also parse these timestamps and should see correct data — accuracy is a server responsibility, not a per-client workaround.
- **Naive bind policy**: chose *promote to UTC* over *reject*. Rationale: existing fixtures and migration scripts predate the project's `_utcnow()` convention; rejecting naive inputs would break current tests without a safety win. Assumption worth revisiting if fixtures ever genuinely carry a non-UTC instant.
- **Client defensive parser kept despite the server fix**: `parseServerDate` stays as a cheap safety net for three concrete scenarios — (1) older responses already cached on disk, (2) a rollback of the server change, (3) future fields we forget to normalize. The plan weighed removing it after deployment but kept it because the cost is four lines of regex and a zero-op branch for designator-bearing strings.
- **FTS handled separately**: `_fts_created_at_to_iso` exists because FTS virtual tables store opaque TEXT and bypass `UtcDateTime`. Considered rewriting the FTS triggers to store ISO with `+00:00`, but that would require a migration and would not retroactively fix existing rows. Normalizing at read time gives identical correctness with zero migration cost.

Open assumptions: production SQLite data is assumed UTC throughout (consistent with `_utcnow()` usage). If a historical row ever came from a non-UTC source, the promotion in `process_result_value` would mislabel it — not expected in this codebase but flagged for future archaeology.

## Result

- All `uv run pytest` suites pass in `packages/cluster` (403 tests, 10 new) and `packages/machine` (213 tests). `packages/agent` has one pre-existing failure (`test_integrate_registers_handler`) unrelated to this change, reproduced on `main`.
- `cd packages/cluster/frontend && npm test --run` passes 108 tests (13 new, across `datetime.test.ts` and `pending-queries.test.ts`).
- `npm run build` passes `tsc` type-check and vite production build.
- Manual verification still pending on a live KST dev server — wiring new messages through WS to confirm the rendered timestamp matches wall-clock and `RoomQueryBanner` shows the new chip.
- Out of scope for this commit: UX tidy-ups for historical terminal chips and question-bubble pending badges — tracked separately in #94 and depends on this change.

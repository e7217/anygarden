# feat(rooms): flip context_window_enabled default to true and gate as admin-only (#225)

- PR: #225
- Date: 2026-04-21
- Branch: `feat/225-room-context-window-default`

## Situation

`Room.context_window_enabled` (#148) was stored with `server_default='0'` and the `RoomEditDialog` exposed the toggle to every room member. Two things drifted from the product intent as #148 Part 3 matured:

- For a multi-agent chat product, the natural UX is "other agents' responses are shared as ambient context by default". A False default meant every fresh room had to be hand-toggled on, or worse, silently ran without cross-agent context because nobody realised the setting existed.
- The toggle materially affects token cost for the whole room (and with `speaker_strategy` now admin-only per #159 Phase C, the dispatch-adjacent `context_window_enabled` was the only member-writable lever that could still balloon costs by a factor of N). It belongs on the admin surface next to the other dispatch controls.

## Task

- Flip the server default to True so new rooms opt into ambient sharing without operator intervention.
- Gate `PATCH /api/v1/rooms/{id}` on `context_window_enabled` with the same admin check that already guards `speaker_strategy` / `orchestrator_agent_id`. Rename-only PATCHes must stay open to non-admin members.
- Hide the toggle from the `RoomEditDialog` for non-admin viewers and omit it from their PATCH payload.
- Preserve existing `False` rows — this is a policy change about defaults, not a data backfill.

## Action

Server:

- `packages/cluster/doorae/db/migrations/versions/028_room_context_window_default_true.py` (new) — `batch_alter_table("rooms")` flips `server_default` from `sa.text("0")` to `sa.text("1")`; downgrade restores `"0"`. No row-level `UPDATE`: explicitly-off rooms stay off.
- `packages/cluster/doorae/db/models.py` — `context_window_enabled` default flipped to `True` / `server_default=sa_text("1")` to match the DDL.
- `packages/cluster/doorae/rooms/router.py` — `RoomOut.context_window_enabled: bool = True`; `update_room` adds `body.context_window_enabled is not None` to `admin_only_fields_present` and updates the docstring + 403 error detail to list the three admin-only fields. `RoomUpdate.context_window_enabled`'s comment now notes the admin-only promotion.
- `packages/cluster/doorae/db/migrations/versions/022_room_context_window.py` — docstring back-reference noting the 028 flip.

Frontend:

- `packages/cluster/frontend/src/components/RoomEditDialog.tsx` — `useState(true)` for `contextWindowEnabled`; the toggle's JSX moved from the always-visible section into the existing `{isAdmin && ...}` block (above the speaker-strategy picker), and the PATCH payload only includes `context_window_enabled` in the admin branch. Copy reworded to make the off-state ("해제하면 ... 토큰을 절약합니다") read naturally next to a default-on checkbox.

Tests:

- `packages/cluster/tests/test_rooms.py::TestRoomContextWindow` — rewrote the class around the new policy. `test_default_is_true` replaces `test_default_is_false`; `test_admin_patch_toggles_context_window` and `test_admin_patch_name_leaves_context_window_unchanged` use a new admin fixture; new `test_non_admin_cannot_change_context_window` asserts 403 + storage invariance; new `test_non_admin_rename_without_flag_succeeds` guards the rename regression path.
- `packages/cluster/tests/test_ws_handler.py::TestContextWindowBroadcast::test_no_stamp_when_flag_off` — explicitly turns the flag off on the fixture room (the default is now True so the test can't rely on it anymore).
- `packages/cluster/tests/test_migrations.py` — bumped the five head-revision assertions from `"027"` to `"028"`.
- `packages/cluster/frontend/src/components/RoomEditDialog.test.tsx` — rewrote around an `installFetch({ isAdmin })` helper. Two admin cases (default-on reflection + admin-off PATCH) replace the old two; new non-admin case asserts the toggle is unmounted and the PATCH body omits all three admin-only fields.

## Decisions

Alternatives weighed in `.tmp/plan-225-room-context-window-default-and-admin.md` §3.2; condensed here.

Existing-row policy — three options:

- **A1. DDL default only, preserve rows (chosen).** Current False rooms are a mix of (a) "created before anybody cared" and (b) "admin intentionally disabled." The migration has no way to tell them apart, so `UPDATE rooms SET context_window_enabled=1` would silently override (b). Admin can still flip individual rooms on via the now admin-only toggle.
- A2. Backfill every row to True — wrong for (b); downgrade can't restore the original values.
- A3. Treat the column as nullable and resolve at read time — the column is already NOT NULL and making it nullable for a defaulting purpose is a larger blast radius than the problem warrants.

Permission scope — three options:

- **B1. Admin-only, matching `speaker_strategy` (chosen).** The #159 Phase C comment in `router.py` already captured the principle: "dispatch-mode controls stay on the admin surface because a mistaken flip silently reroutes who replies." `context_window_enabled` has the same profile (a silent flip changes token cost + how agents receive ambient messages for every turn). Lines up with the existing inline-gate pattern so the review surface is one new condition, not a new dependency.
- B2. Read-only-for-members, admin writable — adds a UI tier with no real investigation benefit since the toggle doesn't expose information members can act on.
- B3. Keep member-writable — rejected by the issue's premise.

UI placement — three options:

- **C1. First field inside the existing `{isAdmin && ...}` block (chosen).** Simplest switch first, then the more complex strategy picker; reuses the existing `border-t` admin-section separator. Zero new component structure.
- C2. Below the strategy picker — technically fine but buries a room-wide cost toggle under a dispatch-specific one.
- C3. A new "Advanced" collapsible — overkill; the `{isAdmin && ...}` block is already the "advanced" surface.

Assumptions that should trigger re-evaluation if violated:

- Most existing rooms with `context_window_enabled=False` are in that state because they predate anyone caring about the toggle (not because an admin explicitly disabled them). If we later learn the B-case is dominant, a one-off backfill script (not a migration) becomes a reasonable follow-up.
- The `DOORAE_CONTEXT_WINDOW_ENABLED` env knob is fully deprecated and no production deployment is still reading it. Otherwise the default flip affects a path we're not observing here.
- The UI copy change ("해제하면 ... 토큰을 절약합니다") accurately conveys that un-checking is the cost-saving direction; if copy testing shows it still reads as "un-checking is the safe default," a dedicated Phase should reword it.

## Result

New rooms opt into ambient context sharing by default; existing rooms keep their stored value. Admin-only PATCH is enforced at the router with matching client-side gating in `RoomEditDialog`. Non-admin members can still rename rooms (verified by regression test). Tests: 688 cluster pass (including the 5 new `TestRoomContextWindow` cases), 283 machine pass, 260 agent pass (the one pre-existing `test_openai.py` failure reproduces on `main` and is unrelated — missing `OPENAI_API_KEY`). Frontend `npm run build` passes and the three `RoomEditDialog.test.tsx` cases (admin reflect, admin PATCH, non-admin hide) pass. Deferred: bulk backfill of existing False rooms, copy-tuning pass on the toggle label.

# feat(agents): customizable avatars (emoji/lucide) and per-agent settings menu (#101)

- Commit: `a879ce4` (a879ce43429f9c70fa8c7e4fc91d558dc5e786da)
- Author: Changyong Um
- Date: 2026-04-18T16:40:22+09:00
- PR: #101

## Situation

PR #99 introduced `EntityAvatar` with seed-driven initials, but agents that share a name prefix were visually indistinguishable, and admins had no way to annotate an agent with a meaningful glyph (role, engine-leaning, project). Meanwhile the `AdminMachines` agent row carried four inline icon buttons — Manage rooms, Edit manifest, Activity, Delete — stacked tight next to the state badge. That's the same "control strip vs glance information" pressure `RoomSettingsMenu` already solved by collapsing admin actions behind a single `⋯` trigger.

## Task

- Let admins pick an emoji or a lucide icon per agent, with a one-click "remove customization" path back to the seed-driven initial.
- Propagate the choice to every surface that already renders an agent avatar (sidebar admin DM list, DM room header, message bubbles, participant popover, admin machines row) without forcing those surfaces to re-fetch `/agents`.
- Make sure avatar edits do NOT restart the agent: avatars are UI metadata and don't flow into the machine-side materializer.
- Collapse the four inline admin-action icons on the agent row into a single overflow menu that mirrors the `RoomSettingsMenu` pattern. Start/Stop stays inline because it's a frequent toggle and the icon communicates state.
- Defer image upload, @lobehub brand-mark bodies, and full lucide search UI to separate issues.

## Action

Backend (`packages/cluster`):
- `db/migrations/versions/017_agent_avatar.py` — new alembic revision adding `avatar_kind VARCHAR(16)` and `avatar_value VARCHAR(64)` to `agents`, both nullable. Uses `batch_alter_table` for SQLite compatibility.
- `db/models.py:137` — Agent model gains the two columns.
- `api/v1/agents.py:48` — `AgentUpdate` accepts `avatar_kind(_set)` / `avatar_value(_set)` with the same explicit-flag idiom as `agents_md_set`. `AgentOut` exposes the fields. The PUT handler keeps two change counters so avatar-only edits skip `bump_generation`; mixed edits still bump via the non-avatar counter.
- `api/v1/machines.py:350` — `MachineAgentOut` carries the avatar fields too, so the admin detail row renders without a secondary lookup.
- `rooms/router.py:76` — `ParticipantOut` propagates avatars for agent participants (users/guests stay null).
- `tests/test_agents_api.py:670` — two new cases: roundtrip set/omit/clear via `*_set` flags, and generation-bump gating (avatar-only = no bump; name + avatar = bump). `tests/test_migrations.py` — revision assertions bumped to `"017"`.

Frontend (`packages/cluster/frontend/src`):
- `lib/avatar-options.ts` — new. `CURATED_EMOJIS` (48), `CURATED_LUCIDE_NAMES` + `LUCIDE_COMPONENTS` map (40 hand-picked icons), `lookupLucideIcon` helper that returns null on unknown names so stale server values fall back to initials.
- `components/EntityAvatar.tsx` — new `avatarKind` / `avatarValue` props. Fallback chain: imageUrl → emoji → lucide → initials. Tone background and engine-glyph overlay stay constant across branches so "same agent = same color" is preserved. +4 new test cases in `EntityAvatar.test.tsx`.
- `components/AvatarPickerDialog.tsx` — new. Tabs (Emoji / Icon / Reset), curated grids, live preview that mirrors EntityAvatar's render branch, Save ships the `*_set` flags. 4 tests in `AvatarPickerDialog.test.tsx` (emoji/lucide staging, Reset → null/null, unchanged → Save disabled). Radix Tabs required `fireEvent.mouseDown` with `button: 0` in tests — documented inline.
- `components/AgentSettingsMenu.tsx` — new. Mirrors `RoomSettingsMenu` pointer/outside-click/ESC behavior; items Edit avatar · Edit manifest · Manage rooms · Activity · Copy agent ID · separator · Delete agent (destructive styling). 6 tests.
- `components/AdminMachines.tsx` — both agent rows (unplaced + placed) gain `EntityAvatar size="md"`, and the four inline admin-action buttons are replaced with `AgentSettingsMenu`. Start/Stop and Retry-placement stay inline. New `avatarDialogOpen`/`avatarAgent` state and `handleEditAvatar`/`handleCopyAgentId` callbacks; `MachineAgent` interface gains the two avatar fields.
- `components/MessageBubble.tsx`, `ParticipantListPopover.tsx`, `RoomHeader.tsx`, `Sidebar.tsx`, `pages/ChatPage.tsx` — each EntityAvatar call site for agents now forwards `avatarKind` / `avatarValue` from the participant / agent record that surface already holds. Non-admin sidebar DM list intentionally left unchanged (no useAgents access; documented as a known limitation — Phase 1 is admin-only customization).
- `hooks/useAgents.ts` — `Agent` interface gains the two fields; `updateAgent` patch type accepts the `*_set` flags.

## Decisions

Rationale was pre-written in `.tmp/plan-101-agent-avatar-and-settings-menu.md`; the decisive threads:

- **Two columns vs single prefixed string vs JSON.** Chose `avatar_kind` + `avatar_value` over `emoji:🤖`-style prefix encoding because the planned follow-up sources (image upload, @lobehub marks) also branch on kind, and a prefix scheme would force every reader to parse. JSON was rejected as overkill for the current shape. Assumption to revisit: if the value field ever needs to be structured (multiple URLs, multiple badges), this split will need JSON after all.
- **`avatar_kind` NULL vs `'initials'` NOT NULL default.** Picked NULL to avoid a data-backfill on existing agents and to sidestep the SQLite enum migration awkwardness 016 already called out. "No customization" is expressible as NULL/NULL; the UI treats it identically to the initials fallback.
- **Skip `bump_generation` on avatar-only edits.** Verified precondition with `grep -r "avatar" packages/machine/ packages/agent/` (no matches), confirming avatar is UI-only metadata and restarting the agent for an emoji swap would be pure surprise. Implemented as a second change counter rather than a blanket early-return so mixed edits (name + avatar) still bump.
- **Curated icon set instead of full lucide search UI.** 40 hand-picked names are explicitly imported so rollup tree-shakes unused icons; a search UI over ~1500 icons would need a dedicated input, filtered list, and probably a bundle split. Treated as a distinct feature for a follow-up issue rather than scope creep here.
- **Avatar picker as a dedicated dialog vs a section in `AgentEditDialog`.** AgentEditDialog owns the per-agent manifest (system prompt + file tree). Avatar edits have a different persistence path (no generation bump), a different affordance (grid vs textarea), and belong next to the ⋯ menu's "Edit avatar" label. Conflating them would mean admins scrolling past a file tree to pick an emoji.
- **Two change counters in the PUT handler vs "early return if only avatar set".** The counter approach composes with future fields without special-casing. Rejected the early-return pattern because it would need to be re-examined every time a new "UI-only" field lands.
- **Radix Tabs in tests.** `fireEvent.click` on a `TabsTrigger` doesn't trigger value change under jsdom — Radix uses `onMouseDown` with `event.button === 0`. Inlined a short comment next to the `fireEvent.mouseDown` calls explaining why, so a reader who sees `mouseDown` on a tab doesn't assume it was a mistake.

## Result

- Admin flow: select a machine → agent row shows `EntityAvatar` + the `⋯` menu; "Edit avatar" opens the picker, pick an emoji or icon (or Reset), Save → server round-trips, every `EntityAvatar` in the session re-renders with the new body on next data refresh. Avatar-only edits do not restart the agent (verified by the generation-bump test).
- Admin actions collapsed: the four buttons per row are now one `⋯` trigger + Start/Stop. Layout is looser and matches `RoomSettingsMenu` behavior, including the destructive red Delete row behind a separator.
- Tests: backend 405 passing (+2), frontend 171 passing (+14 new across EntityAvatar / AvatarPickerDialog / AgentSettingsMenu).
- Out of scope, tracked for follow-ups: image upload source, @lobehub model-logo bodies, user/guest/room avatar customization, full lucide search UI, non-admin agent avatar visibility for DM rooms, custom "are you sure?" overwrite dialog replacing `window.confirm` and `confirm()` in the delete path.

# feat(ui): add seed-based EntityAvatar for agents, DMs, participants, messages (#97)

- Commit: `3c63c7c` (3c63c7cd488d08de9791623af17ce1f04fa97408)
- Author: Changyong Um
- Date: 2026-04-18T15:44:16+09:00
- PR: #97

## Situation

Agents, users, DM partners, and rooms appeared across the UI (sidebar
DM list, chat bubbles, participant popover, agent-rooms dialog, room
header) but were only distinguished by text labels plus the occasional
`Bot`/`Hash` glyph. When several rows stacked together — e.g. the
participant popover or a long chat — scanning for a specific speaker
was slow. Prior brainstorming (see `.tmp/plan-entity-avatar.md`
Context) converged on a deterministic id→color+initials avatar as the
cheapest intervention that delivered most of the perceived value.

## Task

- Add a shared `EntityAvatar` component driven by a stable seed
  (participant id, agent id, or room id) with no server-side schema
  or new dependencies.
- Keep DESIGN.md's warm-neutral + single-accent palette intact:
  avatars must draw from the existing palette rather than forcing
  new saturated colors.
- Cover four surfaces in the first cut: `MessageBubble`,
  `RoomHeader` (DM only), `ParticipantListPopover`, `AgentRoomsDialog`,
  and the `Sidebar` DM rows (both admin and non-admin paths).
- Leave general Hash rooms in the sidebar, pulse-ring animations,
  and topology gradient rings explicitly out of scope.
- Preserve every existing test suite; add unit coverage for the new
  hash/palette/slug logic and the component's branches.

## Action

- `src/lib/avatar.ts` / `avatar.test.ts` — FNV-1a 32-bit hash over the
  seed, modulo an 8-entry palette drawn from DESIGN.md §2 (warm
  neutral + teal / green / orange / pink / purple / brown / notion
  blue). `getInitials()` handles Latin "first-last" names, single
  tokens, and CJK names (single-character initial). 14 tests cover
  determinism, ±30% distribution across 2,000 seeds, and edge cases.
- `src/components/EngineGlyph.tsx` — lifted verbatim from
  `AgentNode.tsx:41-60`, now with an optional `size` prop so avatars
  can render a 9–16 px badge version. `AgentNode.tsx` now imports it
  and drops the OpenAI/Anthropic `@lobehub/icons` imports that were
  never referenced.
- `src/components/EntityAvatar.tsx` / `EntityAvatar.test.tsx` — wraps
  the existing shadcn `<Avatar/>` and renders initials on a
  tone-colored background. For `kind='agent'` with an engine value, a
  small white-bordered `EngineGlyph` badge sits at the bottom-right.
  `kind='guest'` gets a dashed brand-colored border. Size scale:
  xs=20 · sm=24 · md=32 · lg=40.
- `MessageBubble.tsx` — computes an avatar `kind` from
  `isAgent`/`is_anonymous`/`isOrphan`, seeds the tone on
  `participant.id` (or `orphan-${message.id}` for detached rows), and
  inserts a `sm` avatar in each header variant (result / forward /
  isMine / normal). `isMine` mirrors to the right; everyone else sits
  to the left. Four new test cases cover kind derivation and orphan
  seeding.
- `RoomHeader.tsx` + `ChatPage.tsx` — added `isDm` and `dmAgent` props.
  ChatPage derives `dmAgent` from the participants map (not the
  admin-gated `useAgents()`) so non-admin DM viewers still see the
  avatar. When both are set, a `md` agent avatar replaces the left
  Hash icon; general rooms are unchanged.
- `ParticipantListPopover.tsx` — each row gets an `xs` avatar to the
  left of the existing `PresenceDot`. Sort order, badges, and the
  remove button stay untouched.
- `AgentRoomsDialog.tsx` — Assigned and Available rows each gain an
  `sm` room avatar (seed = `room.id`) with the room name truncating to
  the button.
- `Sidebar.tsx` — both DM code paths (admin `AgentDMListAdmin` and
  the non-admin plain list) swap the `Bot` icon for an `xs` agent
  avatar. The admin path passes `engine` from `useAgents()`, so it
  renders the engine badge; the non-admin path omits it (avatar still
  gets a colored initial). The unused `Bot` lucide import was removed.
  Pinned + project-tree Hash rooms are untouched.

## Decisions

Drawn from `.tmp/plan-entity-avatar.md` §3.2 (Decision Log), which
captured several rounds of brainstorming that preceded this commit:

- **`@lobehub/ui` vs. hand-rolled** — rejected lobehub/ui because it
  peer-depends on antd (hundreds of KB gzip) and its default gradient
  avatars collide with DESIGN.md's warm-neutral aesthetic. We only
  needed the Avatar surface; buying the whole theme system was
  asymmetric. Hand-rolling over `@radix-ui/react-avatar` (already a
  dep) gave full control of color and sizing at zero bundle cost.
- **Engine glyph: overlay badge vs. initials-only** — overlay won
  because the topology's `AgentNode` already carries Claude/Codex/
  Gemini marks; reusing them on chat/sidebar avatars keeps the visual
  language consistent across views. A 12 px badge is small enough not
  to compete with the initial.
- **Sidebar general Hash rooms** — deliberately excluded. Indented
  project trees already read well with just `#`, and adding
  per-room avatars there would increase visual density without a
  proportional gain. DMs, by contrast, are 1:1 with a single agent,
  so the avatar carries real identity.
- **Presence dot placement** — kept adjacent (not overlaid inside the
  avatar) so existing callers don't need to restructure their
  `PresenceDot` API or positioning.
- **Palette size: 8 vs. 16 slots** — 8 was enough for realistic room
  sizes and stayed inside DESIGN.md's existing warm palette. 16 would
  have forced us to introduce saturated colors that don't fit the
  warm-neutral baseline.
- **Deferred**: pulse-ring animation on avatars and topology
  gradient rings. Pulse rings would only make sense once we've
  decided to demote the existing `BrailleSpinner`/"응답 대기 중"
  pending-question badge from primary signal to secondary; that's a
  UX decision that needed its own PR. Topology state rings already
  handle "running" visually.
- **Assumption**: `Participant` never carries `engine`, so agent
  avatars in `MessageBubble` and non-admin `Sidebar` DM rows render
  without the corner badge. If `engine` is later threaded through the
  participant payload (or added to `ParticipantOut` server-side), the
  badge appears automatically with no further changes.
- **Assumption**: DM rooms have exactly one agent participant. The
  `dmAgent` derivation in `ChatPage` picks the first agent it finds —
  if the server ever allows multi-agent DMs this needs to switch to
  `representative_agent_id`.

## Result

- 153 / 153 frontend tests pass, `tsc -b` + `vite build` green.
- 16 files changed: 6 new (`EntityAvatar`/`EngineGlyph`/`avatar`
  trio + tests), 9 modified, 1 tsbuildinfo.
- No dependency changes; bundle size effectively unchanged.
- `AgentNode` behavior preserved — the engine-glyph extraction was
  mechanical and its test suite (minus the EngineGlyph section that
  moved to `EngineGlyph.test.tsx`) still passes.
- Follow-ups intentionally left open: surfacing `engine` on
  `Participant` so message bubbles also get engine badges; deciding
  whether pulse-ring animations should replace the BrailleSpinner
  pending-question badge; potentially extending avatars to general
  Hash rooms if user feedback shows the current Hash-only treatment
  is insufficient at scale.

# fix(frontend): let guests autocomplete every participant (#39)

- Commit: `f757dee` (f757dee07b66ba34353436cad2ffb1d6a869db22)
- Author: Changyong Um
- Date: 2026-04-15T17:09:59+09:00
- PR: #39

## Situation

After the guest-participation RFC shipped, guests in `GuestRoomPage` could only `@`-mention agents, and even those mentions rendered as "알 수 없는 사용자" in chat bubbles and failed to wake the intended agent. The rest of the codebase keys participant lookups by `participant.id`, but `GuestRoomPage` was emitting mention tokens keyed to `agent_id`/`user_id`, producing identifiers that no consumer recognised.

## Task

- Stop filtering the mention list down to agents only in the guest shell.
- Emit each mention token with `id: p.id` so downstream consumers (`MessageBubble::resolveUser`, agent `should_respond` via welcome-frame `_my_participant_ids`) can resolve them.
- Keep `#room` autocomplete disabled for guests — the server's guest room scope still forbids it.

## Action

- `packages/cluster/frontend/src/pages/GuestRoomPage.tsx`: dropped the `filter(p => p.kind === 'agent')` and reused the `ChatPage.tsx` `mentionUsers` pattern (`kind: p.kind === 'agent' ? 'agent' : 'user'`, `id: p.id`). Renamed the local `mentionAgents` → `mentionParticipants` for accuracy and updated the `<MessageInput mentionUsers={…}>` pass-through. Replaced the stale "agent-only" comment with a note referencing the server-side guest room scope + `@user` token resolution (design doc §11.6). `mentionRooms={[]}` kept so guests still can't autocomplete rooms.

## Result

Guests can `@`-mention any participant (users, agents, other guests) in their room, and the resulting tokens resolve both in UI rendering and in agent routing. Gates run in the PR: frontend `npm run build` passes (tsc + vite); cluster `uv run ruff check` shows no new warnings vs origin/main (92 pre-existing); cluster `uv run pytest -q` → 316 passed, 1 deselected. Single-file change (+13 / −12 in `GuestRoomPage.tsx`).

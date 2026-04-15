# fix(rooms): route direct-typed #RoomName mentions to target room (#53)

- Commit: `48e813d` (48e813dfd8bf85bc3e003d45086bbc7fbf932941)
- Author: Changyong Um
- Date: 2026-04-15T23:33:28+09:00
- PR: #53

## Situation

Room mentions in the chat composer were only routed correctly when the user picked the room from the autocomplete popover. Directly typing `#테스트룸2` sent the hash-name as plaintext, and the backend `parse_mentions` (which only understands `<#room:id>` ID tokens and legacy `@Name`) could not attach a `room_query` mention to the message. The result: messages directly-typed with `#RoomName` were broadcast only in the current room, and agents in the referenced room never saw or responded to them. The autocomplete path already carried the necessary `mentionRooms` list on the frontend, so the server had no way to know the two paths were diverging.

## Task

- Make direct-typed `#RoomName` produce the same `<#room:id>` payload that autocomplete already emits, without changing the backend parser or any server-side routing.
- Only convert when the mapping is unambiguous (exactly one room matches by display name); otherwise keep plaintext as a safe fallback.
- Leave existing `<#room:...>` tokens untouched, and don't disturb the `@user` mention path (out of scope).
- Add minimal frontend unit-test infra so this pure string logic can be covered and reused by future work (#55).

## Action

- `packages/cluster/frontend/src/lib/mentions.ts`: added a pure `resolveRoomMentionsInText(content, rooms)` helper. It scans for `(^|\s)#([^\s<>#]+)`, trims trailing punctuation, and replaces the match with `<#room:id>` only when exactly one `rooms[i].display === name` is found; duplicates and misses stay as plaintext. No React/DOM dependencies so it is safe to call from anywhere.
- `packages/cluster/frontend/src/components/MessageInput.tsx`: imported the new helper and called it inside `handleSend` after `trackedMentions` replacement and before `extractMentionsMetadata`, so plaintext hashes pick up the same metadata pipeline as autocomplete selections.
- `packages/cluster/frontend/package.json`: added `vitest` devDependency plus `test` (`vitest run`) and `test:watch` scripts.
- `packages/cluster/frontend/vite.config.ts`: added a `/// <reference types="vitest" />` and an inline `test` block (`environment: 'node'`, `include: ['src/**/*.{test,spec}.{ts,tsx}']`) so tests live next to source without a separate config file.
- `packages/cluster/frontend/src/lib/mentions.test.ts`: new suite with 11 cases — single / inline / multi match, duplicate-name fallback, no-match fallback, coexistence with existing tokens, Korean + English + digit names, trailing punctuation, empty-rooms input, `abc#word` non-trigger, and an integration check through `extractMentionsMetadata` / `parseMentionTokens`.

## Result

- Direct-typed `#RoomName` is now tokenized before send, so the existing backend `room_query` routing attaches the representative agent and the target room's participants respond (issue #53 fixed). Autocomplete path is unchanged.
- All 11 Vitest unit tests pass; existing backend `packages/cluster/tests/test_mention_parsing.py` still passes (6/6) — backend surface is unchanged. `npm run build` (tsc + vite) is clean.
- Vitest infra is now available for the frontend package as a shared foundation; follow-up work on #55 can add more tests without further setup. Duplicate room names and unmatched names intentionally remain plaintext as a safety fallback — proper UX for name collisions is left to a future issue.

# feat(tasks): /task slash command in chat input (#269)

- Commit: `2ac3b54` (2ac3b5424eec610e56bedf467663ff8e13bdd7ed)
- Author: Changyong Um
- Date: 2026-04-26T00:01:01+09:00
- PR: #269

## Situation

Phase 1 (#266) made tasks first-class but only through the TaskPanel
"+" composer — to create a task you had to switch tabs out of chat,
type a title, pick an assignee, and click Add. That context switch is
the dominant friction. The most common flow ("@bot, do X") happens in
the chat input itself, but typing a mention there sends a regular
message that the agent might reply to but won't track as work.

## Task

- Let users create + assign + auto-trigger a task in one keystroke
  pattern: `/task @bot 제목` followed by Enter.
- Reuse Phase 1's POST endpoint and synthetic mention pipeline — the
  backend should be untouched for Phase 2.
- Fall through to a normal send for unknown commands so legitimate
  messages starting with `/` (paths, URLs) aren't hijacked.
- Surface inline errors when the input is malformed (no assignee,
  empty title) without introducing a toast library the repo doesn't
  yet have.

## Action

- `lib/slashCommands.ts` (new): a small dispatch layer with
  `parseSlashCommand` (router) and `parseTaskCommand` (per-command
  parser). Returns a `ParseResult<T>` discriminated union so callers
  branch on a single `ok` flag. Strips *all* user-mention tokens from
  the title (the first becomes the assignee; the rest are dropped to
  keep the title clean) but preserves room-mention tokens
  (`<#room:...>`) since they may legitimately appear inside the title.
- `lib/slashCommands.test.ts` (new): 9 unit cases covering normal
  parsing, multi-mention dedupe, missing assignee, empty title,
  whitespace handling, and room-mention preservation.
- `components/MessageInput.tsx`: in `handleSend`, *after* mention
  tokens are resolved (so the parser sees `<@user:pid>`), check for a
  leading `/`. If `parseSlashCommand` returns a known dispatch, fire
  the corresponding API call and clear the input; if the parse
  returned `{ ok: false }`, set `slashError` for the inline banner.
  Unknown commands return `null` and fall through to normal send.
- A `slashError` state + an inline red banner above the textarea.
  `handleChange` clears the banner the moment the user keeps typing.

## Decisions

Slash command infrastructure size — three options weighed in plan
§3.2 결정 1:

- **Single-command inline**: ~50 LOC, parses `/task` directly inside
  `handleSend`. Cheapest now, but the next slash command would force
  a refactor.
- **Small framework** (chosen): ~150 LOC, `parseSlashCommand` routes
  to a per-command parser. The next command (`/handoff`,
  `/summarize`) is one new function. Tests stay focused per command.
- **External library** (`react-slash-commands` etc.): rejected — adds
  a dependency for a feature small enough to own outright, and the
  repo's existing patterns (mentions, handoff) are all hand-rolled.

The framework size pays for itself the first time a second command
arrives. Given doorae's prior `[HANDOFF]`, `[ROOM_QUERY]` patterns,
that's likely.

Mention token semantics in the title — the test suite captures the
exact rule we picked:

- First user-mention token → assignee.
- All other user-mention tokens → stripped from title.
- Room-mention tokens → preserved in title (they're not assignee
  candidates, and may be a legitimate reference to a sibling room).

This is stricter than the legacy chat path (which keeps every
mention in content) because a task title is a single record, not a
conversation. Trying to encode "this task is for A, with cc B" via
two mentions would muddle the recorded title without a clear UI to
back it.

Error surfacing — picked an inline red banner over a floating toast
because the repo has no toast infrastructure (verified by grep:
every existing component uses local `error` state in dialogs). A
new toast lib is out of scope; the inline banner is consistent with
how `LoginForm`, `RoomInviteDialog`, and the existing
`uploadError` slot all surface errors.

Fire-and-forget POST — we clear the input immediately rather than
awaiting the response. The Phase 1 fanout (`task.updated` WS frame +
synthetic mention `MessageOut`) already updates TaskPanel + chat
stream when the POST lands, so there's nothing to render from the
HTTP response itself. Awaiting would only add a Send button latency
the user doesn't need. On HTTP error we set `slashError` from inside
the `.then` — the input was already cleared, so the user sees the
error and re-types if they want.

Assumptions worth flagging:
- Mention popover correctly tokenizes `@AgentName` → `<@user:pid>`
  before submit. Phase 1 confirmed this for the regular send path;
  Phase 2 piggybacks on the same resolution code (`trackedMentions`
  loop + `resolveRoomMentionsInText`). If a future change moves
  tokenization downstream of `handleSend`, the slash interception
  needs to move with it.
- `roomId` is always available when the user can type. The early
  return with `&& roomId` is a defensive belt — unset `roomId` in
  practice means the input is hidden.

## Result

- Frontend `npm run build` green; `vitest` 336/336 (was 327; +9 new
  slash-parser cases).
- Typing `/task <@user:pid> 제목` + Enter creates the task, kicks
  the assignee agent through the existing mention path, and renders
  the synthetic task-card in chat — all from the chat input,
  zero tab switching.
- Malformed inputs (`/task` alone, `/task <@user:pid>` with no
  title) surface a red banner and don't send.
- Unknown slash commands (`/foo`, `/path/to/x`) fall through to a
  normal chat message, preserving existing behavior.
- Backend touched: 0 files. Phase 2 is purely a frontend feature
  on top of Phase 1's wiring — exactly the layering the plan
  promised.

# feat(chat): open routine and goal forms from /routine and /goal (#940)

- Commit: `ef77515` (ef7751556342b1651d00193131e511f1963c335f)
- Author: Changyong Um
- Date: 2026-10-27T12:09:04+09:00
- PR: — (issue #940)

## Situation

The composer supported exactly one slash command (`/task`), and even that was undiscoverable — typing `/` showed no suggestions. Users wanting to create a routine or goal had to navigate to the right rail manually. Additionally, a latent security gap existed: Pi engine adapters expand input starting with `/` as templates or skills, so a chat message whose text happened to begin with `/` could accidentally be interpreted as an engine command.

## Task

- Add `/routine [@agent] [title]` and `/goal [title]` commands that open the matching right-rail form pre-filled in a dialog, instead of sending a message.
- Surface all available commands in a suggestion list when the user types `/` (or a prefix thereof).
- Guard the two new commands: when the room has no agents (routine) or the user lacks campaign-start permission (goal), keep the text in the input and display a reason.
- Threads and guest sessions must not offer the two commands; `/routine`/`/goal` typed there sends as plain text.
- Prefix any user chat content that begins with `/` with `Message: ` before handing it to engine adapters, so it can never be misread as a template or skill invocation.

## Action

- **`packages/cluster/frontend/src/lib/slashCommands.ts`**: Added `RoutineCommandPayload`, `GoalCommandPayload`, `parseRoutineCommand`, `parseGoalCommand`, `SlashFormRequest`, `SlashCommandInfo`, `SLASH_COMMANDS` registry, and `matchSlashCommands(input)`. Widened `SlashDispatch` from a generic `{ command: string; parsed: ParseResult<T> }` to a narrowed discriminated union covering all three commands. `parseSlashCommand` now handles `routine` and `goal` via a `switch` and uses `input.search(/\s/)` to correctly split on any whitespace.
- **`packages/cluster/frontend/src/components/MessageInput.tsx`**: Added `onSlashForm` prop (`(request: SlashFormRequest) => string | null`). Extended `MentionState.type` to include `'/'`. On every keystroke, when the whole input matches `/[a-z]*`, a `/`-type mention is opened and `matchSlashCommands` feeds the filtered suggestion list — `/task` always appears, the others only when `onSlashForm` is provided. Selecting a suggestion fills `/{name} ` and focuses the textarea. On submit, if the dispatched command is not `task` and `onSlashForm` is present, it is called; a truthy return (blocked reason) is shown as a slash error and submission stops; a null return clears the input normally.
- **`packages/cluster/frontend/src/components/slash/SlashFormDialog.tsx`** (new): Renders a `<Dialog>` wrapping `<GoalForm>` for `/routine` and passes through `<CampaignForm>` for `/goal`, reusing the exact same form components the right rail uses. After creation it fires `anygarden:goal:updated` so the rail list refreshes.
- **`packages/cluster/frontend/src/components/campaign/campaignAccess.ts`** (new): Exports `canStartCampaign(room, participants, user)` access check, extracted from `CampaignSection`, so `ChatPage` can gate the `/goal` command.
- **`packages/cluster/frontend/src/pages/ChatPage.tsx`**: Wires `onSlashForm` into `<MessageInput>`. Implements the access checks: no agents → routine blocked with i18n message; `!canStartCampaign(room, participants, user)` → goal blocked; otherwise opens `<SlashFormDialog>` with the request.
- **`packages/cluster/anygarden/agent/integrations/base.py`** (`assemble_user_content`, line ~410): Prefixes `labelled_content` with `Message: ` when it starts with `/`, preventing engine command confusion.
- **i18n** (`packages/cluster/frontend/src/i18n/catalogs/chat.ts`): Added keys `chat.slash.task`, `chat.slash.routine`, `chat.slash.goal`, `chat.slash.routineTitle`, `chat.slash.routineDescription`, `chat.slash.routineNoAgents`, `chat.slash.goalUnavailable`.
- **Tests**: `slashCommands.test.ts` covers `matchSlashCommands`, `parseRoutineCommand`, `parseGoalCommand`; `MessageInput.test.tsx` covers suggestion rendering and `onSlashForm` callback behaviour; `SlashFormDialog.test.tsx` covers dialog open/close; `test_speaker_attribution.py` covers the `Message: ` prefix path. New requirement rows MSG-10 (vitest) and ENG-14 (pytest) in `docs/requirements.md`; `GoalsSection` now shares `routineAgentOptions` from `lib/goals.ts`.

## Decisions

- **Form-in-dialog vs. dedicated route**: Opening the existing right-rail forms in a `<Dialog>` overlay was chosen over a separate route because it reuses validation, submission, and error handling with zero duplication, and keeps the user on the same page. The alternative — a dedicated `/routine/new` route — would require navigation and back-handling.
- **`onSlashForm` prop pattern vs. context/event**: Passing a callback prop rather than a context value or a DOM event keeps the access logic in `ChatPage` (the only layer with room and user state) while keeping `MessageInput` generic enough to be used in threads and guest shells without modification. Threads simply omit `onSlashForm`.
- **`canStartCampaign` extraction**: The access check was in `CampaignSection`'s render logic. Extracting it to `campaignAccess.ts` avoids importing the full section component into `ChatPage` just to read a boolean.
- **Engine `Message:` prefix**: Prefixing in `assemble_user_content` (the single point where chat text becomes engine input) is the safest minimal fix; it avoids touching any parser and is a no-op for messages that don't start with `/`. Alternative — disabling slash expansion entirely for chat messages — was rejected as too broad and would break legitimate Pi template usage from the agent side.
- Assumption: if Pi ever starts using `Message:` as a command prefix, this guard needs revisiting.

## Result

- Typing `/` in a project room composer now shows a suggestion list of `/task`, `/routine`, `/goal` with descriptions.
- `/routine [@agent] [title]` opens the routine form pre-filled; `/goal [title]` opens the campaign form pre-filled. Both close on submit or cancel and refresh the right-rail list.
- Rooms with no agents show an inline error for `/routine`; rooms where the user lacks campaign permission show one for `/goal`.
- Thread and guest composers are unaffected (no `onSlashForm` prop wired).
- Chat messages beginning with `/` can no longer be mistaken for engine commands.
- 483 lines added, 45 removed across 18 files; all new paths covered by unit tests.

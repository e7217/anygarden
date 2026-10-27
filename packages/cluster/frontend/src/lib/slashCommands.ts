/**
 * Slash command framework (#269).
 *
 * Tiny dispatch layer for in-input commands like ``/task @bot title``.
 * ``/task`` creates a task directly; ``/routine`` and ``/goal`` (#940)
 * open the matching form pre-filled. The framework stays small: adding
 * a command is a parser plus one ``SLASH_COMMANDS`` entry.
 *
 * Parsers return a discriminated union (`{ ok: true, payload }` or
 * `{ ok: false, error }`) so callers can branch on a single check
 * and surface a user-facing error inline.
 */

export type ParseResult<T> =
  | { ok: true; payload: T }
  | { ok: false; error: string }

/** Payload produced by ``/task`` after extracting the assignee mention
 * and the trailing title text. */
export interface TaskCommandPayload {
  /** Assignee participant id (room participant uuid). Required —
   * Phase 2 always assigns at create time. */
  assignee_pid: string
  /** Task title, with the leading mention stripped. */
  title: string
}

const ID_MENTION_RE = /<@user:([^>]+)>/
const ALL_USER_MENTIONS_RE = /<@user:[^>]+>/g

/** Parse the ``/task`` body (everything after the ``/task `` prefix).
 *
 * Format: ``<@user:{pid}> rest of the title`` — the first
 * ID-based user mention is the assignee; the remainder (after stripping
 * that token) is the task title. Subsequent mention tokens are
 * preserved inside the title since they may legitimately reference
 * other participants in the title text.
 *
 * Note: this function is exported so it can be unit-tested in isolation
 * from the React input shell.
 */
export function parseTaskCommand(body: string): ParseResult<TaskCommandPayload> {
  const match = body.match(ID_MENTION_RE)
  if (!match) {
    return { ok: false, error: 'assignee가 필요합니다 — @로 에이전트를 지정해주세요.' }
  }
  const pid = match[1]
  // Strip *all* user-mention tokens from the title. Only the first
  // becomes the assignee; the rest would muddle the title text and
  // are not surfaced as additional structure (slash command semantics
  // are exactly one assignee per task). Room-mention tokens
  // (``<#room:...>``) are deliberately left in place — they may
  // legitimately appear inside the title.
  const title = body.replace(ALL_USER_MENTIONS_RE, '').replace(/\s+/g, ' ').trim()
  if (!title) {
    return { ok: false, error: 'title이 비어 있습니다.' }
  }
  return { ok: true, payload: { assignee_pid: pid, title } }
}

/** Payload of ``/routine`` (#940): an optional assignee and text. Text
 * asks an agent for a draft (#948); without it the empty form opens. */
export interface RoutineCommandPayload {
  assignee_pid: string | null
  /** What the routine should do, in the person's words. */
  text: string
}

/** Payload of ``/goal`` (#940): text asks the lead for a draft (#947);
 * without it the empty form opens. */
export interface GoalCommandPayload {
  text: string
}

const stripUserMentions = (body: string) =>
  body.replace(ALL_USER_MENTIONS_RE, '').replace(/\s+/g, ' ').trim()

/** Parse the ``/routine`` body. Both parts are optional. */
export function parseRoutineCommand(body: string): ParseResult<RoutineCommandPayload> {
  const match = body.match(ID_MENTION_RE)
  return { ok: true, payload: { assignee_pid: match ? match[1] : null, text: stripUserMentions(body) } }
}

/** Parse the ``/goal`` body: whatever is left describes the goal. */
export function parseGoalCommand(body: string): ParseResult<GoalCommandPayload> {
  return { ok: true, payload: { text: stripUserMentions(body) } }
}

export type SlashDispatch =
  | { command: 'task'; parsed: ParseResult<TaskCommandPayload> }
  | { command: 'routine'; parsed: ParseResult<RoutineCommandPayload> }
  | { command: 'goal'; parsed: ParseResult<GoalCommandPayload> }

export type SlashCommandName = SlashDispatch['command']

/** What the composer hands its host for ``/routine`` and ``/goal``. */
export type SlashFormRequest =
  | { command: 'routine'; payload: RoutineCommandPayload }
  | { command: 'goal'; payload: GoalCommandPayload }

export interface SlashCommandInfo {
  name: SlashCommandName
  /** What to type, shown in the suggestion list. */
  usage: string
  /** i18n key of the one-line description. */
  descriptionKey: 'chat.slash.task' | 'chat.slash.routine' | 'chat.slash.goal'
}

export const SLASH_COMMANDS: readonly SlashCommandInfo[] = [
  { name: 'task', usage: '/task @agent title', descriptionKey: 'chat.slash.task' },
  { name: 'routine', usage: '/routine @agent description', descriptionKey: 'chat.slash.routine' },
  { name: 'goal', usage: '/goal description', descriptionKey: 'chat.slash.goal' },
]

/** Commands to suggest while the user is still typing the command word
 * (``/``, ``/ro``). Nothing once a space follows or no command matches. */
export function matchSlashCommands(input: string): SlashCommandInfo[] {
  if (!/^\/[a-z]*$/.test(input)) return []
  const query = input.slice(1)
  return SLASH_COMMANDS.filter(c => c.name.startsWith(query))
}

/** Look up the command from the input and route to its parser.
 *
 * Returns ``null`` for inputs that aren't slash commands at all OR for
 * unknown command names — callers then fall through to a normal send,
 * which lets users type messages that *happen* to start with ``/`` (a
 * URL fragment, a file path) without slash interpretation.
 */
export function parseSlashCommand(input: string): SlashDispatch | null {
  if (!input.startsWith('/')) return null
  const space = input.search(/\s/)
  const name = space === -1 ? input.slice(1) : input.slice(1, space)
  const body = space === -1 ? '' : input.slice(space + 1)
  switch (name) {
    case 'task': return { command: 'task', parsed: parseTaskCommand(body) }
    case 'routine': return { command: 'routine', parsed: parseRoutineCommand(body) }
    case 'goal': return { command: 'goal', parsed: parseGoalCommand(body) }
    default: return null
  }
}

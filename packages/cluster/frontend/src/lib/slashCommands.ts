/**
 * Slash command framework (#269).
 *
 * Tiny dispatch layer for in-input commands like ``/task @bot title``.
 * The framework intentionally stays small — Phase 2 ships exactly one
 * command (``/task``); the abstraction is just enough to make adding
 * a second command (``/handoff``, ``/summarize``…) a one-file change.
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

export interface SlashDispatch<T> {
  command: string
  parsed: ParseResult<T>
}

/** Look up the command from the input and route to its parser.
 *
 * Returns ``null`` for inputs that aren't slash commands at all OR for
 * unknown command names — callers then fall through to a normal send,
 * which lets users type messages that *happen* to start with ``/`` (a
 * URL fragment, a file path) without slash interpretation.
 */
export function parseSlashCommand(
  input: string,
): SlashDispatch<TaskCommandPayload> | null {
  if (!input.startsWith('/')) return null
  const space = input.indexOf(' ')
  const name = space === -1 ? input.slice(1) : input.slice(1, space)
  const body = space === -1 ? '' : input.slice(space + 1)
  if (name === 'task') {
    return { command: 'task', parsed: parseTaskCommand(body) }
  }
  return null
}

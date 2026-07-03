// frontend/src/lib/datetime.ts
//
// Issue #93 — ECMAScript parses an ISO 8601 date-time string without a
// timezone designator as *local* time. Historically the server (SQLite
// + naive Pydantic serialization) emitted strings like
// ``"2026-04-17T05:12:03.456789"`` for UTC instants, which KST(+9)
// clients misread as nine hours in the past — breaking the pending
// TTL filter in ``pending-queries.ts`` and miscolouring message
// timestamps in ``MessageBubble``.
//
// After the server fix lands every datetime response carries a
// designator, but this helper is kept as a defensive parser so:
//
//   * older messages cached on disk / in memory still parse correctly,
//   * a rollback of the server change doesn't re-introduce the bug,
//   * future fields we forget to normalize stay safe.

/** Parse a server-emitted ISO 8601 datetime string as UTC when the
 * input lacks a timezone designator. Strings with ``Z`` or ``±HH:MM``
 * (with or without a colon) pass through unchanged. */
export function parseServerDate(input: string): Date {
  if (/[Zz]|[+\-]\d{2}:?\d{2}$/.test(input)) return new Date(input)
  return new Date(input + 'Z')
}

/** Issue #512 — format a server timestamp for a chat message bubble.
 *
 * A message sent on the viewer's local calendar day shows only the time
 * (e.g. ``14:23``), preserving the previous behaviour. An older message
 * is prefixed with its date so the reader can place it without scrolling:
 *
 *   * same year   → ``6월 30일 14:23``
 *   * other year  → ``2025년 12월 30일 14:23``
 *
 * The time portion reuses the existing ``toLocaleTimeString`` rendering,
 * so today's messages look identical to before. ``now`` is injectable
 * for deterministic tests. Returns ``''`` on unparseable input, matching
 * the prior fail-safe in ``MessageBubble``. */
export function formatMessageTimestamp(iso: string, now: Date = new Date()): string {
  let d: Date
  try {
    d = parseServerDate(iso)
  } catch {
    return ''
  }
  if (Number.isNaN(d.getTime())) return ''

  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate()
  if (sameDay) return time

  const monthDay = `${d.getMonth() + 1}월 ${d.getDate()}일`
  const date = d.getFullYear() === now.getFullYear() ? monthDay : `${d.getFullYear()}년 ${monthDay}`
  return `${date} ${time}`
}

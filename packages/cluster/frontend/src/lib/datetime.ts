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

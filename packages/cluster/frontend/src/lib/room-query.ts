// frontend/src/lib/room-query.ts
//
// Selectors for the three ``room_query*`` metadata families that
// the server attaches to messages once issue #55 lands:
//
//   * ``room_query``        — the originating user message in the
//                             source room. Carries ``role="question"``,
//                             ``query_id``, ``target_room_id``,
//                             ``source_room_id``, ``source_participant_id``.
//                             Banner uses this to seed a pending chip.
//   * ``room_query_forward`` — the ``[ROOM_QUERY] ...`` message the
//                             representative agent broadcasts into
//                             the target room. The target-room
//                             bubble strips the prefix and renders
//                             a ``↪ #source · @author`` badge.
//   * ``room_query_result``  — the ``[취합 결과]`` summary the
//                             representative ships back to the
//                             source room. Carries per-response
//                             cards + status (completed / timeout /
//                             solo) so the banner chip resolves and
//                             the result card can render structured
//                             output.
//
// Pure functions only — no React, no DOM. ``ChatArea`` and
// ``MessageBubble`` consume these selectors to decide which
// variant to render.

import type { ChatMessage } from '@/hooks/useWebSocket'

export type RoomQueryStatus = 'pending' | 'completed' | 'timeout' | 'solo'

/** ``metadata.room_query`` — the originating user question. */
export interface RoomQueryQuestionMeta {
  query_id: string
  target_room_id: string
  source_room_id: string
  source_participant_id?: string | null
}

/** ``metadata.room_query_forward`` — the broadcast into the target room. */
export interface RoomQueryForwardMeta {
  query_id: string
  source_room_id: string
  source_participant_id?: string | null
}

/** A single agent's reply collected by the representative. */
export interface RoomQueryResponseEntry {
  participant_id: string
  content: string
  /** #153 — sender's display_name snapshot captured by the
   * representative agent at reply time. Preferred over
   * ``participantNames`` lookups in the source room because
   * the responder is typically in a *different* room. Absent on
   * legacy payloads and when the sender wasn't in the candidate
   * snapshot — the render-side fallback chain handles both. */
  name?: string
}

/** ``metadata.room_query_result`` — synthesized result back in the source room. */
export interface RoomQueryResultMeta {
  query_id: string
  target_room_id: string
  responded: number
  expected: number
  status: RoomQueryStatus
  responses: RoomQueryResponseEntry[]
}

function readMetaObject(msg: ChatMessage, key: string): Record<string, unknown> | null {
  const meta = msg.metadata
  if (!meta || typeof meta !== 'object') return null
  const value = (meta as Record<string, unknown>)[key]
  if (!value || typeof value !== 'object') return null
  return value as Record<string, unknown>
}

/**
 * Return the structured ``room_query`` (question) metadata if this
 * message is the originating user question, else ``null``.
 *
 * Required fields: ``query_id``, ``target_room_id``, ``source_room_id``.
 * Pre-#55 ``room_query`` blobs without a ``query_id`` (or with
 * ``role`` other than ``"question"``) deliberately return ``null``
 * — the banner has no token to pair such messages against, so
 * suppressing them keeps the chip count honest.
 */
export function parseQuestion(msg: ChatMessage): RoomQueryQuestionMeta | null {
  const raw = readMetaObject(msg, 'room_query')
  if (!raw) return null
  // Only the role="question" variant is the originating user message.
  // The post-#55 ``handler.py`` always sets it; legacy blobs without
  // ``role`` are treated as legacy and skipped.
  if (raw.role !== 'question') return null
  const query_id = typeof raw.query_id === 'string' ? raw.query_id : ''
  const target_room_id = typeof raw.target_room_id === 'string' ? raw.target_room_id : ''
  const source_room_id = typeof raw.source_room_id === 'string' ? raw.source_room_id : ''
  if (!query_id || !target_room_id || !source_room_id) return null
  const source_participant_id =
    typeof raw.source_participant_id === 'string' ? raw.source_participant_id : null
  return { query_id, target_room_id, source_room_id, source_participant_id }
}

/**
 * Return ``room_query_forward`` metadata for messages broadcast into
 * the target room (the ``[ROOM_QUERY] ...`` bubble), else ``null``.
 *
 * ``source_participant_id`` may legitimately be ``null`` for legacy
 * messages — the badge falls back to ``↪ #room`` without the user
 * suffix in that case.
 */
export function parseForward(msg: ChatMessage): RoomQueryForwardMeta | null {
  const raw = readMetaObject(msg, 'room_query_forward')
  if (!raw) return null
  const query_id = typeof raw.query_id === 'string' ? raw.query_id : ''
  const source_room_id = typeof raw.source_room_id === 'string' ? raw.source_room_id : ''
  if (!query_id || !source_room_id) return null
  const source_participant_id =
    typeof raw.source_participant_id === 'string' ? raw.source_participant_id : null
  return { query_id, source_room_id, source_participant_id }
}

/**
 * Return ``room_query_result`` metadata for the synthesized summary
 * delivered back to the source room, else ``null``.
 *
 * ``responses`` defaults to ``[]`` (solo path) — the card knows how
 * to render an empty list with the ``status="solo"`` header.
 */
export function parseResult(msg: ChatMessage): RoomQueryResultMeta | null {
  const raw = readMetaObject(msg, 'room_query_result')
  if (!raw) return null
  const query_id = typeof raw.query_id === 'string' ? raw.query_id : ''
  const target_room_id = typeof raw.target_room_id === 'string' ? raw.target_room_id : ''
  const status = raw.status
  if (!query_id || !target_room_id) return null
  if (status !== 'completed' && status !== 'timeout' && status !== 'solo') {
    return null
  }
  const responded = typeof raw.responded === 'number' ? raw.responded : 0
  const expected = typeof raw.expected === 'number' ? raw.expected : 0
  const responsesRaw = Array.isArray(raw.responses) ? raw.responses : []
  const responses: RoomQueryResponseEntry[] = []
  for (const entry of responsesRaw) {
    if (!entry || typeof entry !== 'object') continue
    const e = entry as Record<string, unknown>
    const pid = typeof e.participant_id === 'string' ? e.participant_id : ''
    const content = typeof e.content === 'string' ? e.content : ''
    const parsed: RoomQueryResponseEntry = { participant_id: pid, content }
    if (typeof e.name === 'string') {
      parsed.name = e.name
    }
    responses.push(parsed)
  }
  return { query_id, target_room_id, responded, expected, status, responses }
}

/** Strip the ``[ROOM_QUERY] `` prefix the server still emits on the
 * forwarded bubble. Body remains prefixed on the wire so
 * ``should_respond``'s startswith path keeps working — the strip is
 * purely a render-time concern (plan §6.1). Tolerant of trailing
 * whitespace variations (``[ROOM_QUERY]hello`` and
 * ``[ROOM_QUERY]   hello`` both come out as ``hello``). */
export function stripRoomQueryPrefix(content: string): string {
  return content.replace(/^\[ROOM_QUERY\]\s*/, '')
}

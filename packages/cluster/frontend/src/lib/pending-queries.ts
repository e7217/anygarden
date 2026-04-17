// frontend/src/lib/pending-queries.ts
//
// Pure derivation of banner chips from the message stream. Extracted
// from ``ChatArea`` (issue #66) so the TTL filter is unit-testable
// without mounting React.
//
// Why TTL? Agent processes can die before ``COLLECT_TIMEOUT`` (5 min)
// elapses, which means no ``room_query_result`` is ever emitted and
// the ``pending`` chip becomes a permanent ghost on reload. This
// filter drops orphan pending entries whose question is older than
// ``PENDING_TTL_MS``. The number is ``COLLECT_TIMEOUT + 2 min``
// network/commit/broadcast slack so legitimate slow completions at
// T+5 still resolve the chip before it's culled.
//
// The TTL only applies to ``status === 'pending'`` with no
// ``result_message_id``. Timeout / solo / completed states are
// server-confirmed terminal outcomes and are rendered regardless of
// how old the question is.

import type { ChatMessage } from '@/hooks/useWebSocket'
import type { PendingQuery } from '@/components/RoomQueryBanner'
import { parseQuestion, parseResult } from './room-query'
import { parseServerDate } from './datetime'

/** Agent-side ``COLLECT_TIMEOUT`` is 5 min; we add 2 min slack for
 * network / DB commit / broadcast so legitimate slow results at
 * T+5:30 still upgrade the chip before TTL culls it. */
export const PENDING_TTL_MS = 7 * 60 * 1000

/** Internal bag — ``_question_created_at`` is stripped before the
 * function returns so ``RoomQueryBanner`` never sees it. */
interface PendingQueryInternal extends PendingQuery {
  _question_created_at?: string
}

/** Per-query aggregation derived from the message stream. Pure
 * function — the banner state rebuilds from ``messages`` on every
 * render, which is O(N) but bounded by the 100-message history
 * window the server ships. This also gives us automatic
 * reconnect-restore for free: if the user reloads the page, the
 * history fetch seeds the same view.
 *
 * ``dismissedIds`` come from the banner state so user-acknowledged
 * timeouts / solos stay hidden. ``now`` is injected so the caller's
 * ``useMemo`` controls when recomputation happens (passing
 * ``Date.now()`` inside deps would thrash). */
export function buildPendingQueries(
  messages: ChatMessage[],
  currentRoomId: string,
  dismissedIds: Set<string>,
  roomNameLookup: (id: string) => string | undefined,
  now: Date,
): PendingQuery[] {
  // question side seeds a pending entry; a later result upgrades
  // status/counts and records the result_message_id so the
  // completed chip knows where to scroll.
  const byQuery = new Map<string, PendingQueryInternal>()
  for (const msg of messages) {
    // Only show chips in the *source* room — the room where the
    // user asked the question. The target room's forward bubble
    // is not a banner concern.
    if (msg.room_id !== currentRoomId) continue

    const q = parseQuestion(msg)
    if (q) {
      const existing = byQuery.get(q.query_id)
      if (!existing) {
        byQuery.set(q.query_id, {
          query_id: q.query_id,
          target_room_id: q.target_room_id,
          target_room_name:
            roomNameLookup(q.target_room_id) ?? `${q.target_room_id.slice(-6)}`,
          status: 'pending',
          responded: 0,
          expected: 0,
          _question_created_at: msg.created_at,
        })
      }
      continue
    }

    const r = parseResult(msg)
    if (r) {
      const existing = byQuery.get(r.query_id)
      const merged: PendingQueryInternal = {
        query_id: r.query_id,
        target_room_id: r.target_room_id,
        target_room_name:
          existing?.target_room_name ??
          roomNameLookup(r.target_room_id) ??
          `${r.target_room_id.slice(-6)}`,
        status: r.status,
        responded: r.responded,
        expected: r.expected,
        result_message_id: msg.id,
        // preserve the question timestamp if we've seen it; not
        // required for terminal states but keeps the bag internally
        // consistent for future debug / instrumentation.
        _question_created_at: existing?._question_created_at,
      }
      byQuery.set(r.query_id, merged)
    }
  }

  // Apply TTL + dismissed filters, then strip the internal field.
  const out: PendingQuery[] = []
  const nowMs = now.getTime()
  for (const entry of byQuery.values()) {
    if (dismissedIds.has(entry.query_id)) continue

    // TTL: orphan pending (no result) older than PENDING_TTL_MS is
    // dropped. ``parseServerDate`` treats designator-less ISO strings
    // as UTC so a KST browser doesn't misread server-emitted
    // timestamps as nine hours in the past (issue #93). Malformed
    // timestamps parse to NaN → the comparison below is false →
    // entry is kept (defensive: better to show a stale chip than
    // accidentally hide a valid one).
    if (
      entry.status === 'pending' &&
      !entry.result_message_id &&
      entry._question_created_at
    ) {
      const questionMs = parseServerDate(entry._question_created_at).getTime()
      if (Number.isFinite(questionMs) && nowMs - questionMs > PENDING_TTL_MS) {
        continue
      }
    }

    // Strip internal-only field so the banner never sees it.
    const { _question_created_at: _omit, ...cleanEntry } = entry
    void _omit
    out.push(cleanEntry)
  }
  return out
}

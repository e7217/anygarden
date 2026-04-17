import { describe, it, expect } from 'vitest'
import { buildPendingQueries, PENDING_TTL_MS } from './pending-queries'
import type { ChatMessage } from '@/hooks/useWebSocket'

/** Factory mirroring room-query.test.ts ``msg()`` pattern. */
function msg(extra: Partial<ChatMessage> = {}): ChatMessage {
  return {
    type: 'message',
    id: 'm1',
    room_id: 'room-a',
    participant_id: 'pid',
    content: '',
    seq: 1,
    created_at: new Date().toISOString(),
    ...extra,
  }
}

function question(overrides: Partial<ChatMessage> & { query_id: string; target_room_id: string; created_at: string; id?: string; room_id?: string }) {
  return msg({
    id: overrides.id ?? `q-${overrides.query_id}`,
    room_id: overrides.room_id ?? 'room-a',
    created_at: overrides.created_at,
    metadata: {
      room_query: {
        role: 'question',
        query_id: overrides.query_id,
        target_room_id: overrides.target_room_id,
        source_room_id: 'room-a',
        source_participant_id: 'user-pid',
      },
    },
  })
}

function result(
  overrides: Partial<ChatMessage> & {
    query_id: string
    target_room_id: string
    status: 'completed' | 'timeout' | 'solo'
    responded?: number
    expected?: number
    id?: string
    room_id?: string
    created_at?: string
  },
) {
  return msg({
    id: overrides.id ?? `r-${overrides.query_id}`,
    room_id: overrides.room_id ?? 'room-a',
    created_at: overrides.created_at ?? new Date().toISOString(),
    metadata: {
      room_query_result: {
        query_id: overrides.query_id,
        target_room_id: overrides.target_room_id,
        status: overrides.status,
        responded: overrides.responded ?? 0,
        expected: overrides.expected ?? 0,
        responses: [],
      },
    },
  })
}

const lookup = (id: string) => (id === 't1' ? 'Target Room' : undefined)

describe('buildPendingQueries', () => {
  it('returns empty array on empty messages', () => {
    const now = new Date()
    const out = buildPendingQueries([], 'room-a', new Set(), lookup, now)
    expect(out).toEqual([])
  })

  it('ignores messages from other rooms', () => {
    const now = new Date()
    const msgs = [
      question({
        query_id: 'q1',
        target_room_id: 't1',
        created_at: now.toISOString(),
        room_id: 'room-other',
      }),
    ]
    const out = buildPendingQueries(msgs, 'room-a', new Set(), lookup, now)
    expect(out).toEqual([])
  })

  it('includes a recent pending chip (1 minute old)', () => {
    const now = new Date('2026-04-15T12:00:00Z')
    const oneMinAgo = new Date(now.getTime() - 60 * 1000).toISOString()
    const msgs = [
      question({ query_id: 'q1', target_room_id: 't1', created_at: oneMinAgo }),
    ]
    const out = buildPendingQueries(msgs, 'room-a', new Set(), lookup, now)
    expect(out).toHaveLength(1)
    expect(out[0].query_id).toBe('q1')
    expect(out[0].status).toBe('pending')
    // Internal field must not leak.
    expect(
      (out[0] as unknown as Record<string, unknown>)._question_created_at,
    ).toBeUndefined()
  })

  it('excludes orphan pending chip older than TTL (8 minutes, no result)', () => {
    const now = new Date('2026-04-15T12:00:00Z')
    const eightMinAgo = new Date(now.getTime() - 8 * 60 * 1000).toISOString()
    const msgs = [
      question({ query_id: 'q1', target_room_id: 't1', created_at: eightMinAgo }),
    ]
    const out = buildPendingQueries(msgs, 'room-a', new Set(), lookup, now)
    expect(out).toEqual([])
  })

  it('includes completed chip even when question is 8 minutes old (result upgrade wins)', () => {
    const now = new Date('2026-04-15T12:00:00Z')
    const eightMinAgo = new Date(now.getTime() - 8 * 60 * 1000).toISOString()
    const msgs = [
      question({ query_id: 'q1', target_room_id: 't1', created_at: eightMinAgo }),
      result({
        query_id: 'q1',
        target_room_id: 't1',
        status: 'completed',
        responded: 2,
        expected: 2,
      }),
    ]
    const out = buildPendingQueries(msgs, 'room-a', new Set(), lookup, now)
    expect(out).toHaveLength(1)
    expect(out[0].status).toBe('completed')
    expect(out[0].result_message_id).toBe('r-q1')
  })

  it('includes old timeout chip (TTL applies only to pending)', () => {
    const now = new Date('2026-04-15T12:00:00Z')
    const eightMinAgo = new Date(now.getTime() - 8 * 60 * 1000).toISOString()
    const msgs = [
      question({ query_id: 'q1', target_room_id: 't1', created_at: eightMinAgo }),
      result({
        query_id: 'q1',
        target_room_id: 't1',
        status: 'timeout',
        responded: 1,
        expected: 3,
      }),
    ]
    const out = buildPendingQueries(msgs, 'room-a', new Set(), lookup, now)
    expect(out).toHaveLength(1)
    expect(out[0].status).toBe('timeout')
  })

  it('includes old solo chip (TTL applies only to pending)', () => {
    const now = new Date('2026-04-15T12:00:00Z')
    const eightMinAgo = new Date(now.getTime() - 8 * 60 * 1000).toISOString()
    const msgs = [
      question({ query_id: 'q1', target_room_id: 't1', created_at: eightMinAgo }),
      result({
        query_id: 'q1',
        target_room_id: 't1',
        status: 'solo',
        responded: 0,
        expected: 0,
      }),
    ]
    const out = buildPendingQueries(msgs, 'room-a', new Set(), lookup, now)
    expect(out).toHaveLength(1)
    expect(out[0].status).toBe('solo')
  })

  it('excludes entries present in dismissedIds', () => {
    const now = new Date('2026-04-15T12:00:00Z')
    const oneMinAgo = new Date(now.getTime() - 60 * 1000).toISOString()
    const msgs = [
      question({ query_id: 'q1', target_room_id: 't1', created_at: oneMinAgo }),
    ]
    const out = buildPendingQueries(
      msgs,
      'room-a',
      new Set(['q1']),
      lookup,
      now,
    )
    expect(out).toEqual([])
  })

  it('boundary: 6 min 59 sec old → included', () => {
    const now = new Date('2026-04-15T12:00:00Z')
    const justUnder = new Date(
      now.getTime() - (PENDING_TTL_MS - 1000),
    ).toISOString()
    const msgs = [
      question({ query_id: 'q1', target_room_id: 't1', created_at: justUnder }),
    ]
    const out = buildPendingQueries(msgs, 'room-a', new Set(), lookup, now)
    expect(out).toHaveLength(1)
  })

  it('boundary: 7 min 1 sec old → excluded', () => {
    const now = new Date('2026-04-15T12:00:00Z')
    const justOver = new Date(
      now.getTime() - (PENDING_TTL_MS + 1000),
    ).toISOString()
    const msgs = [
      question({ query_id: 'q1', target_room_id: 't1', created_at: justOver }),
    ]
    const out = buildPendingQueries(msgs, 'room-a', new Set(), lookup, now)
    expect(out).toEqual([])
  })

  it('defensive: malformed created_at (unparseable) → included', () => {
    const now = new Date('2026-04-15T12:00:00Z')
    const msgs = [
      question({
        query_id: 'q1',
        target_room_id: 't1',
        created_at: 'not-a-date',
      }),
    ]
    const out = buildPendingQueries(msgs, 'room-a', new Set(), lookup, now)
    expect(out).toHaveLength(1)
    expect(out[0].status).toBe('pending')
  })

  // Issue #93 regression — the historical server emitted ISO strings
  // without a timezone designator for UTC instants. ``new Date`` reads
  // those as local time, so in KST every fresh pending question looked
  // nine hours old and was culled by the TTL filter. ``parseServerDate``
  // must treat bare strings as UTC so the chip survives.
  it('regression #93: TZ-less ISO 1 min ago is treated as UTC and included', () => {
    const now = new Date('2026-04-17T05:13:00Z')
    const oneMinAgoNoTz = '2026-04-17T05:12:00'
    const msgs = [
      question({ query_id: 'q1', target_room_id: 't1', created_at: oneMinAgoNoTz }),
    ]
    const out = buildPendingQueries(msgs, 'room-a', new Set(), lookup, now)
    expect(out).toHaveLength(1)
    expect(out[0].status).toBe('pending')
  })

  it('regression #93: TZ-less ISO 8 min ago is still excluded by TTL', () => {
    const now = new Date('2026-04-17T05:20:00Z')
    const eightMinAgoNoTz = '2026-04-17T05:12:00'
    const msgs = [
      question({ query_id: 'q1', target_room_id: 't1', created_at: eightMinAgoNoTz }),
    ]
    const out = buildPendingQueries(msgs, 'room-a', new Set(), lookup, now)
    expect(out).toEqual([])
  })
})

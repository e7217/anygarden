import { describe, it, expect } from 'vitest'
import {
  parseQuestion,
  parseForward,
  parseResult,
  stripRoomQueryPrefix,
} from './room-query'
import type { ChatMessage } from '@/hooks/useWebSocket'

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

describe('parseQuestion', () => {
  it('returns metadata when role=question and required fields present', () => {
    const m = msg({
      metadata: {
        room_query: {
          role: 'question',
          query_id: 'q1',
          target_room_id: 't1',
          source_room_id: 's1',
          source_participant_id: 'user-pid',
        },
      },
    })
    expect(parseQuestion(m)).toEqual({
      query_id: 'q1',
      target_room_id: 't1',
      source_room_id: 's1',
      source_participant_id: 'user-pid',
    })
  })

  it('returns null for legacy room_query without role', () => {
    // Pre-#55 in-flight messages have no ``role``/``query_id`` —
    // they should not seed banner chips (no token to dismiss them).
    const m = msg({
      metadata: {
        room_query: { target_room_id: 't1', source_room_id: 's1' },
      },
    })
    expect(parseQuestion(m)).toBeNull()
  })

  it('returns null when query_id missing', () => {
    const m = msg({
      metadata: {
        room_query: { role: 'question', target_room_id: 't1', source_room_id: 's1' },
      },
    })
    expect(parseQuestion(m)).toBeNull()
  })

  it('returns null on plain message with no metadata', () => {
    expect(parseQuestion(msg())).toBeNull()
  })
})

describe('parseForward', () => {
  it('returns forward metadata when query_id and source_room_id present', () => {
    const m = msg({
      metadata: {
        room_query_forward: {
          query_id: 'q1',
          source_room_id: 's1',
          source_participant_id: 'user-pid',
        },
      },
    })
    expect(parseForward(m)).toEqual({
      query_id: 'q1',
      source_room_id: 's1',
      source_participant_id: 'user-pid',
      source_participant_name: null,
    })
  })

  it('tolerates missing source_participant_id (legacy)', () => {
    const m = msg({
      metadata: { room_query_forward: { query_id: 'q1', source_room_id: 's1' } },
    })
    const result = parseForward(m)
    expect(result).not.toBeNull()
    expect(result?.source_participant_id).toBeNull()
  })

  it('returns null when query_id missing', () => {
    const m = msg({ metadata: { room_query_forward: { source_room_id: 's1' } } })
    expect(parseForward(m)).toBeNull()
  })

  it('preserves source_participant_name when present (issue #155)', () => {
    const m = msg({
      metadata: {
        room_query_forward: {
          query_id: 'q1',
          source_room_id: 's1',
          source_participant_id: 'user-pid',
          source_participant_name: 'Alice',
        },
      },
    })
    const result = parseForward(m)
    expect(result).not.toBeNull()
    expect(result?.source_participant_name).toBe('Alice')
  })

  it('sets source_participant_name to null when server omitted it (pre-#155)', () => {
    const m = msg({
      metadata: {
        room_query_forward: {
          query_id: 'q1',
          source_room_id: 's1',
          source_participant_id: 'user-pid',
        },
      },
    })
    const result = parseForward(m)
    expect(result).not.toBeNull()
    expect(result?.source_participant_name).toBeNull()
  })
})

describe('parseResult', () => {
  it('returns completed result with responses array', () => {
    const m = msg({
      metadata: {
        room_query_result: {
          query_id: 'q1',
          target_room_id: 't1',
          status: 'completed',
          responded: 2,
          expected: 2,
          responses: [
            { participant_id: 'a1', content: 'hello' },
            { participant_id: 'a2', content: 'world' },
          ],
        },
      },
    })
    const result = parseResult(m)
    expect(result?.status).toBe('completed')
    expect(result?.responses).toHaveLength(2)
    expect(result?.responses[0]).toEqual({ participant_id: 'a1', content: 'hello' })
  })

  it('returns timeout status with partial responses', () => {
    const m = msg({
      metadata: {
        room_query_result: {
          query_id: 'q1',
          target_room_id: 't1',
          status: 'timeout',
          responded: 1,
          expected: 3,
          responses: [{ participant_id: 'a1', content: 'partial' }],
        },
      },
    })
    expect(parseResult(m)?.status).toBe('timeout')
  })

  it('returns solo status with empty responses', () => {
    const m = msg({
      metadata: {
        room_query_result: {
          query_id: 'q1',
          target_room_id: 't1',
          status: 'solo',
          responded: 0,
          expected: 0,
          responses: [],
        },
      },
    })
    expect(parseResult(m)?.status).toBe('solo')
    expect(parseResult(m)?.responses).toEqual([])
  })

  it('preserves response name field from server payload', () => {
    // #153 — the representative agent now includes the sender's
    // display_name at serialization time so cross-room result cards
    // can render real names instead of @last-6-hex fallbacks.
    const m = msg({
      metadata: {
        room_query_result: {
          query_id: 'q1',
          target_room_id: 't1',
          status: 'completed',
          responded: 1,
          expected: 1,
          responses: [
            { participant_id: 'a1', name: 'Alice', content: 'hi' },
          ],
        },
      },
    })
    const result = parseResult(m)
    expect(result?.responses[0]).toEqual({
      participant_id: 'a1',
      name: 'Alice',
      content: 'hi',
    })
  })

  it('omits name field when server payload lacks it (legacy/wire-compat)', () => {
    // Pre-#153 payloads and any reply from an off-snapshot sender
    // (empty/missing name) must survive parsing without ``name``
    // showing up as the literal string 'undefined' or crashing.
    const m = msg({
      metadata: {
        room_query_result: {
          query_id: 'q1',
          target_room_id: 't1',
          status: 'completed',
          responded: 1,
          expected: 1,
          responses: [{ participant_id: 'a1', content: 'hi' }],
        },
      },
    })
    const result = parseResult(m)
    expect(result?.responses[0]).toEqual({ participant_id: 'a1', content: 'hi' })
    expect(result?.responses[0].name).toBeUndefined()
  })

  it('rejects unknown status values', () => {
    const m = msg({
      metadata: {
        room_query_result: {
          query_id: 'q1',
          target_room_id: 't1',
          status: 'in_progress',  // not one of completed/timeout/solo
          responded: 0,
          expected: 1,
          responses: [],
        },
      },
    })
    expect(parseResult(m)).toBeNull()
  })
})

describe('stripRoomQueryPrefix', () => {
  it('strips the prefix with single space', () => {
    expect(stripRoomQueryPrefix('[ROOM_QUERY] hello')).toBe('hello')
  })

  it('strips the prefix with no trailing space', () => {
    expect(stripRoomQueryPrefix('[ROOM_QUERY]hello')).toBe('hello')
  })

  it('strips multiple trailing spaces', () => {
    expect(stripRoomQueryPrefix('[ROOM_QUERY]   hello')).toBe('hello')
  })

  it('leaves unprefixed content untouched', () => {
    expect(stripRoomQueryPrefix('hello world')).toBe('hello world')
  })

  it('does not strip mid-string occurrence', () => {
    expect(stripRoomQueryPrefix('say [ROOM_QUERY] later')).toBe(
      'say [ROOM_QUERY] later',
    )
  })
})

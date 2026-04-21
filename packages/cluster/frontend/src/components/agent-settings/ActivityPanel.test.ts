// @vitest-environment jsdom
// Unit tests for the #222 turn group-by / outcome derivation helpers.
// The ActivityPanel component itself is UI-thin once ``splitLogs``
// gets the grouping right, so we pin the pure function here.
import { describe, it, expect } from 'vitest'

import { splitLogs } from './ActivityPanel'

interface Row {
  id: string
  event_type: string
  timestamp: string
  request_id: string | null
  details: Record<string, unknown> | null
}

function row(partial: Partial<Row>): Row {
  return {
    id: partial.id ?? 'evt-' + Math.random().toString(36).slice(2, 8),
    event_type: partial.event_type ?? 'message_received',
    timestamp: partial.timestamp ?? '2026-04-21T12:00:00Z',
    request_id: partial.request_id ?? null,
    details: partial.details ?? null,
  }
}

describe('splitLogs', () => {
  it('partitions system events from turn events', () => {
    const logs = [
      row({ event_type: 'start_requested', request_id: null }),
      row({ event_type: 'message_received', request_id: 'r1' }),
      row({ event_type: 'stop_requested', request_id: null }),
    ]
    const { turns, system } = splitLogs(logs)
    expect(system.map(r => r.event_type)).toEqual([
      'start_requested',
      'stop_requested',
    ])
    expect(turns).toHaveLength(1)
    expect(turns[0].requestId).toBe('r1')
  })

  it('groups a full turn and sorts events chronologically', () => {
    // Arrive out of order — service endpoint returns DESC timestamps,
    // but a turn's internal order should be ASC for readability.
    const logs = [
      row({
        id: 'c',
        event_type: 'handler_finished',
        request_id: 'r1',
        timestamp: '2026-04-21T12:00:03Z',
      }),
      row({
        id: 'a',
        event_type: 'message_received',
        request_id: 'r1',
        timestamp: '2026-04-21T12:00:00Z',
        details: { trigger_message_id: 'msg-xyz' },
      }),
      row({
        id: 'b',
        event_type: 'handler_started',
        request_id: 'r1',
        timestamp: '2026-04-21T12:00:01Z',
      }),
    ]
    const { turns } = splitLogs(logs)
    expect(turns).toHaveLength(1)
    expect(turns[0].events.map(e => e.id)).toEqual(['a', 'b', 'c'])
    expect(turns[0].triggerMessageId).toBe('msg-xyz')
    // 3 seconds of transition.
    expect(turns[0].lastTs - turns[0].firstTs).toBe(3_000)
  })

  it('labels outcome by terminal event', () => {
    const cases: Array<{ events: string[]; expected: string }> = [
      {
        events: ['message_received', 'handler_started', 'response_sent', 'handler_finished'],
        expected: 'responded',
      },
      {
        events: ['message_received', 'handler_started', 'handler_finished'],
        expected: 'silent',
      },
      {
        events: ['message_received', 'handler_started', 'handler_orphaned'],
        expected: 'orphaned',
      },
      {
        events: ['message_received', 'handler_started'],
        expected: 'in_flight',
      },
    ]
    for (const [i, { events, expected }] of cases.entries()) {
      const rid = `r${i}`
      const logs = events.map((e, k) =>
        row({
          event_type: e,
          request_id: rid,
          timestamp: `2026-04-21T12:00:0${k}Z`,
        }),
      )
      const { turns } = splitLogs(logs)
      expect(turns[0].outcome, `case ${expected}`).toBe(expected)
    }
  })

  it('orders turns most-recent-first', () => {
    const logs = [
      row({
        event_type: 'message_received',
        request_id: 'old',
        timestamp: '2026-04-21T11:00:00Z',
      }),
      row({
        event_type: 'message_received',
        request_id: 'new',
        timestamp: '2026-04-21T12:00:00Z',
      }),
    ]
    const { turns } = splitLogs(logs)
    expect(turns.map(t => t.requestId)).toEqual(['new', 'old'])
  })

  it('handles an empty log list', () => {
    const { turns, system } = splitLogs([])
    expect(turns).toEqual([])
    expect(system).toEqual([])
  })
})

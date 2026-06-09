// @vitest-environment jsdom
// Unit tests for the #222 turn group-by / outcome derivation helpers.
// The ActivityPanel component itself is UI-thin once ``splitLogs``
// gets the grouping right, so we pin the pure function here.
import { describe, it, expect } from 'vitest'

import { splitLogs, turnLabel } from './ActivityPanel'

interface Row {
  id: string
  event_type: string
  timestamp: string
  request_id: string | null
  agent_id?: string | null
  details: Record<string, unknown> | null
}

function row(partial: Partial<Row>): Row {
  return {
    id: partial.id ?? 'evt-' + Math.random().toString(36).slice(2, 8),
    event_type: partial.event_type ?? 'message_received',
    timestamp: partial.timestamp ?? '2026-04-21T12:00:00Z',
    request_id: partial.request_id ?? null,
    agent_id: partial.agent_id ?? null,
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

  // #429 — room-level view needs the owning agent per turn.
  it('captures the owning agent_id on a turn', () => {
    const logs = [
      row({ event_type: 'message_received', request_id: 'rA', agent_id: 'agent-1', timestamp: '2026-04-21T12:00:00Z' }),
      row({ event_type: 'handler_finished', request_id: 'rA', agent_id: 'agent-1', timestamp: '2026-04-21T12:00:01Z', details: { outcome: 'ok' } }),
      row({ event_type: 'message_received', request_id: 'rB', agent_id: 'agent-2', timestamp: '2026-04-21T12:00:02Z' }),
    ]
    const { turns } = splitLogs(logs)
    const byReq = Object.fromEntries(turns.map(t => [t.requestId, t.agentId]))
    expect(byReq['rA']).toBe('agent-1')
    expect(byReq['rB']).toBe('agent-2')
  })

  // #431 — A→B causal link: the triggering turn's id rides on
  // message_received.details.parent_request_id.
  it('captures parentRequestId from message_received details', () => {
    const logs = [
      row({
        event_type: 'message_received',
        request_id: 'rB',
        timestamp: '2026-04-21T12:00:02Z',
        details: { parent_request_id: 'rA', trigger_message_id: 'msg-1' },
      }),
      row({
        event_type: 'message_received',
        request_id: 'rA',
        timestamp: '2026-04-21T12:00:00Z',
      }),
    ]
    const { turns } = splitLogs(logs)
    const byReq = Object.fromEntries(turns.map(t => [t.requestId, t.parentRequestId]))
    expect(byReq['rB']).toBe('rA')
    expect(byReq['rA']).toBeNull()
  })

  // #425 — the UI now consumes the authoritative details fields.
  it('extracts engine / duration_ms / outcome / error from details', () => {
    const logs = [
      row({ event_type: 'message_received', request_id: 'r1', timestamp: '2026-04-21T12:00:00Z', details: { room_id: 'room-9' } }),
      row({ event_type: 'engine_call_started', request_id: 'r1', timestamp: '2026-04-21T12:00:01Z', details: { engine: 'codex' } }),
      row({ event_type: 'engine_call_finished', request_id: 'r1', timestamp: '2026-04-21T12:00:02Z', details: { engine: 'codex', outcome: 'failed', duration_ms: 4200, error: 'model 400' } }),
      row({ event_type: 'handler_finished', request_id: 'r1', timestamp: '2026-04-21T12:00:02Z', details: { outcome: 'failed', duration_ms: 4300, error: 'model 400' } }),
    ]
    const { turns } = splitLogs(logs)
    const t = turns[0]
    expect(t.engine).toBe('codex')
    expect(t.durationMs).toBe(4300) // handler_finished is the turn time
    expect(t.finalOutcome).toBe('failed')
    expect(t.roomId).toBe('room-9')
    expect(t.error).toBe('model 400')
  })

  it('labels a #422 failed turn as failed, not responded', () => {
    // After #422 a failed turn emits an error-notice response_sent, so
    // the event-presence heuristic would wrongly say 'responded'. The
    // authoritative handler_finished.outcome must win.
    const logs = [
      row({ event_type: 'message_received', request_id: 'r1', timestamp: '2026-04-21T12:00:00Z' }),
      row({ event_type: 'handler_started', request_id: 'r1', timestamp: '2026-04-21T12:00:01Z' }),
      row({ event_type: 'response_sent', request_id: 'r1', timestamp: '2026-04-21T12:00:02Z' }),
      row({ event_type: 'handler_finished', request_id: 'r1', timestamp: '2026-04-21T12:00:02Z', details: { outcome: 'failed' } }),
    ]
    const { turns } = splitLogs(logs)
    expect(turns[0].outcome).toBe('responded') // legacy heuristic unchanged
    expect(turns[0].finalOutcome).toBe('failed')
    expect(turnLabel(turns[0])).toBe('failed') // authoritative display
  })

  it('turnLabel maps ok→responded and falls back to heuristic', () => {
    const ok = splitLogs([
      row({ event_type: 'handler_finished', request_id: 'r1', timestamp: '2026-04-21T12:00:00Z', details: { outcome: 'ok' } }),
    ]).turns[0]
    expect(turnLabel(ok)).toBe('responded')
    const legacy = splitLogs([
      row({ event_type: 'handler_orphaned', request_id: 'r2', timestamp: '2026-04-21T12:00:00Z' }),
    ]).turns[0]
    expect(legacy.finalOutcome).toBeNull()
    expect(turnLabel(legacy)).toBe('orphaned')
  })
})

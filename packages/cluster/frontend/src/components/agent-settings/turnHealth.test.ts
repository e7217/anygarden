import { describe, expect, it } from 'vitest'
import { splitLogs, type ActivityLog } from './ActivityPanel'
import { classifyTurnFailure, latestTurnHealth, summaryErrorCode } from './turnHealth'

let seq = 0
function row(requestId: string, event_type: string, timestamp: string, details: Record<string, unknown> = {}): ActivityLog {
  seq += 1
  return { id: `r${seq}`, event_type, timestamp, request_id: requestId, details }
}

function turns(...rows: ActivityLog[]) {
  return splitLogs(rows).turns
}

describe('classifyTurnFailure', () => {
  it.each([
    ['ENGINE_AUTH_ERROR', 'model_connection'],
    ['PI_PROVIDER_ERROR', 'model_connection'],
    ['AUTH_MISSING', 'model_connection'],
    ['UNKNOWN_PROVIDER', 'model_connection'],
    ['AUTH_CHECK_FAILED', 'model_connection'],
    ['POLICY_DENIED', 'policy'],
    ['TIMEOUT_STOPPED', 'timeout'],
    ['missing_terminal_event', 'engine'],
    ['UNSUPPORTED_RUNTIME: pi 0.85.0 found, 0.85.1 required', 'engine'],
    ['ENGINE_ERROR', 'engine'],
    ['something the engine printed', 'engine'],
  ])('%s → %s', (error, category) => {
    expect(classifyTurnFailure(error, 'failed')).toBe(category)
  })

  it('uses the outcome when no error code is reported', () => {
    expect(classifyTurnFailure(null, 'timeout')).toBe('timeout')
    expect(classifyTurnFailure(null, 'rejected')).toBe('busy')
    expect(classifyTurnFailure(null, 'failed')).toBe('engine')
  })
})

describe('summaryErrorCode', () => {
  it('keeps closed codes and drops free-form text', () => {
    expect(summaryErrorCode('POLICY_DENIED')).toBe('POLICY_DENIED')
    expect(summaryErrorCode('missing_terminal_event')).toBe('missing_terminal_event')
    expect(summaryErrorCode('UNSUPPORTED_RUNTIME: pi 0.85.0 found')).toBe('UNSUPPORTED_RUNTIME')
    expect(summaryErrorCode('Error: 401 {"key":"sk-secret"}')).toBeNull()
    expect(summaryErrorCode(null)).toBeNull()
  })
})

describe('latestTurnHealth', () => {
  it('returns none without turns', () => {
    expect(latestTurnHealth([]).status).toBe('none')
  })

  it('reports the newest turn, not an older success', () => {
    const health = latestTurnHealth(turns(
      row('old', 'handler_started', '2026-09-28T01:00:00.000000Z'),
      row('old', 'response_sent', '2026-09-28T01:00:01.000000Z'),
      row('old', 'handler_finished', '2026-09-28T01:00:02.000000Z', { outcome: 'ok' }),
      row('new', 'handler_started', '2026-09-28T02:00:00.000000Z'),
      row('new', 'handler_finished', '2026-09-28T02:00:03.000000Z', { outcome: 'failed', error: 'missing_terminal_event' }),
    ))
    expect(health).toMatchObject({ status: 'failed', category: 'engine', code: 'missing_terminal_event' })
    expect(health.turn?.requestId).toBe('new')
    expect(health.at).toEqual(new Date('2026-09-28T02:00:03.000Z'))
  })

  it('treats the #422 failure notice as a failure, not a response', () => {
    const health = latestTurnHealth(turns(
      row('t', 'handler_started', '2026-09-28T02:00:00.000000Z'),
      row('t', 'response_sent', '2026-09-28T02:00:01.000000Z'),
      row('t', 'handler_finished', '2026-09-28T02:00:02.000000Z', { outcome: 'failed', error: 'POLICY_DENIED' }),
    ))
    expect(health).toMatchObject({ status: 'failed', category: 'policy', code: 'POLICY_DENIED' })
  })

  it('distinguishes succeeded, in-flight and cancelled turns', () => {
    expect(latestTurnHealth(turns(
      row('a', 'handler_started', '2026-09-28T02:00:00.000000Z'),
      row('a', 'handler_finished', '2026-09-28T02:00:02.000000Z', { outcome: 'ok' }),
    )).status).toBe('succeeded')
    expect(latestTurnHealth(turns(
      row('b', 'message_received', '2026-09-28T02:00:00.000000Z'),
      row('b', 'handler_started', '2026-09-28T02:00:01.000000Z'),
    )).status).toBe('in_flight')
    expect(latestTurnHealth(turns(
      row('c', 'handler_started', '2026-09-28T02:00:00.000000Z'),
      row('c', 'handler_finished', '2026-09-28T02:00:02.000000Z', { outcome: 'cancelled' }),
    )).status).toBe('cancelled')
  })

  it('flags an orphaned turn as failed', () => {
    expect(latestTurnHealth(turns(
      row('o', 'handler_started', '2026-09-28T02:00:00.000000Z'),
      row('o', 'handler_orphaned', '2026-09-28T02:05:00.000000Z'),
    ))).toMatchObject({ status: 'failed', category: 'engine' })
  })
})

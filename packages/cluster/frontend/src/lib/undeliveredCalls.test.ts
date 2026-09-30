import { describe, it, expect } from 'vitest'
import { extractUndeliveredCalls } from './undeliveredCalls'

describe('extractUndeliveredCalls', () => {
  it('returns nothing without the field', () => {
    expect(extractUndeliveredCalls(undefined)).toEqual([])
    expect(extractUndeliveredCalls({})).toEqual([])
    expect(extractUndeliveredCalls({ peer_call_undelivered: 'x' })).toEqual([])
  })

  it('groups targets by reason in first-seen order', () => {
    expect(
      extractUndeliveredCalls({
        peer_call_undelivered: [
          { participant_id: 'a1', reason: 'already_answering' },
          { participant_id: 'a3', reason: 'limit_reached' },
          { participant_id: 'a2', reason: 'already_answering' },
        ],
      }),
    ).toEqual([
      { reason: 'already_answering', participantIds: ['a1', 'a2'] },
      { reason: 'limit_reached', participantIds: ['a3'] },
    ])
  })

  it('skips malformed entries, unknown reasons and duplicates', () => {
    expect(
      extractUndeliveredCalls({
        peer_call_undelivered: [
          null,
          'a1',
          { participant_id: '', reason: 'already_answering' },
          { participant_id: 'a1', reason: 'other' },
          { participant_id: 'a1', reason: 'already_answering' },
          { participant_id: 'a1', reason: 'already_answering' },
        ],
      }),
    ).toEqual([{ reason: 'already_answering', participantIds: ['a1'] }])
  })
})

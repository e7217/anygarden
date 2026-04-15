import { describe, it, expect } from 'vitest'
import { mergePresencePatch } from './useParticipantPresence'

/**
 * Pure merge semantics for ``useParticipantPresence`` (#54).
 *
 * The DOM event plumbing is deliberately not covered here — jsdom
 * reflects the real browser but doesn't test anything the type
 * system hasn't already. What *does* earn its keep is the merge
 * policy: "same state → skip churn" is the contract downstream
 * memoised consumers rely on.
 */
describe('mergePresencePatch', () => {
  it('inserts a new entry', () => {
    const prev = {}
    const next = mergePresencePatch(prev, {
      room_id: 'r',
      participant_id: 'p1',
      online: true,
      last_seen_at: '2026-04-15T12:00:00Z',
    })
    expect(next).not.toBe(prev)
    expect(next.p1).toEqual({
      online: true,
      lastSeenAt: '2026-04-15T12:00:00Z',
    })
  })

  it('flips an existing entry offline', () => {
    const prev = {
      p1: { online: true, lastSeenAt: '2026-04-15T12:00:00Z' },
    }
    const next = mergePresencePatch(prev, {
      room_id: 'r',
      participant_id: 'p1',
      online: false,
      last_seen_at: '2026-04-15T12:05:00Z',
    })
    expect(next.p1.online).toBe(false)
    expect(next.p1.lastSeenAt).toBe('2026-04-15T12:05:00Z')
  })

  it('returns the same reference when nothing changed', () => {
    const prev = {
      p1: { online: true, lastSeenAt: null },
    }
    const next = mergePresencePatch(prev, {
      room_id: 'r',
      participant_id: 'p1',
      online: true,
      last_seen_at: null,
    })
    // Identity equality — downstream memoised consumers skip re-renders.
    expect(next).toBe(prev)
  })

  it('treats missing last_seen_at as null', () => {
    const prev = {}
    const next = mergePresencePatch(prev, {
      room_id: 'r',
      participant_id: 'p1',
      online: false,
    })
    expect(next.p1.lastSeenAt).toBeNull()
  })
})

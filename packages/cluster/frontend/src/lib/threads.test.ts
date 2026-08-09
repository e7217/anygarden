import { describe, it, expect } from 'vitest'
import {
  indexThreads,
  replyCount,
  lastReplyAt,
  threadParticipantIds,
  canHostThread,
} from './threads'
import type { ChatMessage } from '@/hooks/useWebSocket'

function msg(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    type: 'message',
    id: 'm1',
    room_id: 'r1',
    participant_id: 'p1',
    content: '',
    seq: 1,
    created_at: '2026-08-09T00:00:00Z',
    ...overrides,
  }
}

/** A reply carries the same id in both columns — the server shape. */
function reply(rootId: string, overrides: Partial<ChatMessage> = {}): ChatMessage {
  return msg({
    parent_message_id: rootId,
    root_message_id: rootId,
    ...overrides,
  })
}

describe('indexThreads', () => {
  it('keeps top-level messages in the timeline and files replies apart', () => {
    const root = msg({ id: 'root', seq: 1 })
    const other = msg({ id: 'other', seq: 2 })
    const r1 = reply('root', { id: 'r1', seq: 3 })

    const index = indexThreads([root, other, r1])

    expect(index.roots.map(m => m.id)).toEqual(['root', 'other'])
    expect(index.repliesByRoot.get('root')?.map(m => m.id)).toEqual(['r1'])
  })

  it('orders replies by seq regardless of arrival order', () => {
    const root = msg({ id: 'root', seq: 1 })
    const late = reply('root', { id: 'late', seq: 9 })
    const early = reply('root', { id: 'early', seq: 4 })

    const index = indexThreads([root, late, early])

    expect(index.repliesByRoot.get('root')?.map(m => m.id)).toEqual([
      'early',
      'late',
    ])
  })

  it('keeps an orphaned reply visible when its root is outside the window', () => {
    // History is capped at 100 messages, so a reply can outlive the
    // root's presence in the loaded stream. Dropping it would lose
    // user-visible content.
    const orphan = reply('scrolled-away', { id: 'orphan', seq: 5 })

    const index = indexThreads([orphan])

    expect(index.roots.map(m => m.id)).toEqual(['orphan'])
    expect(index.repliesByRoot.size).toBe(0)
  })

  it('marks a surfaced orphan so callers can tell it apart from a real root', () => {
    const realRoot = msg({ id: 'root', seq: 1 })
    const orphan = reply('scrolled-away', { id: 'orphan', seq: 5 })

    const index = indexThreads([realRoot, orphan])

    expect(index.orphanIds.has('orphan')).toBe(true)
    expect(index.orphanIds.has('root')).toBe(false)
  })

  it('does not mark a reply as an orphan when its root is loaded', () => {
    const index = indexThreads([
      msg({ id: 'root', seq: 1 }),
      reply('root', { id: 'r1', seq: 2 }),
    ])

    expect(index.orphanIds.size).toBe(0)
  })

  it('returns empty structures for an empty stream', () => {
    const index = indexThreads([])
    expect(index.roots).toEqual([])
    expect(index.repliesByRoot.size).toBe(0)
  })
})

describe('replyCount', () => {
  it('counts replies and reports zero for a bare root', () => {
    const root = msg({ id: 'root', seq: 1 })
    const bare = msg({ id: 'bare', seq: 2 })
    const index = indexThreads([
      root,
      bare,
      reply('root', { id: 'r1', seq: 3 }),
      reply('root', { id: 'r2', seq: 4 }),
    ])

    expect(replyCount(index, 'root')).toBe(2)
    expect(replyCount(index, 'bare')).toBe(0)
    expect(replyCount(index, 'nonexistent')).toBe(0)
  })
})

describe('lastReplyAt', () => {
  it('reports the newest reply timestamp', () => {
    const index = indexThreads([
      msg({ id: 'root', seq: 1 }),
      reply('root', { id: 'r1', seq: 2, created_at: '2026-08-09T01:00:00Z' }),
      reply('root', { id: 'r2', seq: 3, created_at: '2026-08-09T02:00:00Z' }),
    ])

    expect(lastReplyAt(index, 'root')).toBe('2026-08-09T02:00:00Z')
  })

  it('returns null when the thread has no replies', () => {
    const index = indexThreads([msg({ id: 'root', seq: 1 })])
    expect(lastReplyAt(index, 'root')).toBeNull()
  })
})

describe('threadParticipantIds', () => {
  it('lists the root author first, then repliers in first-appearance order', () => {
    const root = msg({ id: 'root', seq: 1, participant_id: 'author' })
    const index = indexThreads([
      root,
      reply('root', { id: 'r1', seq: 2, participant_id: 'bob' }),
      reply('root', { id: 'r2', seq: 3, participant_id: 'author' }),
      reply('root', { id: 'r3', seq: 4, participant_id: 'carol' }),
    ])

    expect(threadParticipantIds(index, root)).toEqual([
      'author',
      'bob',
      'carol',
    ])
  })

  it('skips messages whose sender was removed from the room', () => {
    const root = msg({ id: 'root', seq: 1, participant_id: null })
    const index = indexThreads([
      root,
      reply('root', { id: 'r1', seq: 2, participant_id: null }),
      reply('root', { id: 'r2', seq: 3, participant_id: 'bob' }),
    ])

    expect(threadParticipantIds(index, root)).toEqual(['bob'])
  })
})

describe('canHostThread', () => {
  it('refuses an orphaned reply and allows a genuine root', () => {
    // The server rejects a thread rooted at a reply
    // ("Thread root must be a top-level message"), so surfacing a reply
    // affordance on an orphan would be a control that always fails.
    const index = indexThreads([
      msg({ id: 'root', seq: 1 }),
      reply('scrolled-away', { id: 'orphan', seq: 2 }),
    ])

    expect(canHostThread(index, 'root')).toBe(true)
    expect(canHostThread(index, 'orphan')).toBe(false)
  })

  it('allows an id the index has never seen', () => {
    // Absence of evidence isn't evidence of an orphan — an unknown id
    // is treated as threadable and the server remains the authority.
    const index = indexThreads([])
    expect(canHostThread(index, 'unknown')).toBe(true)
  })
})

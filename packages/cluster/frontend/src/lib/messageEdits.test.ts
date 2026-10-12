import { describe, expect, it } from 'vitest'
import type { ChatMessage } from '@/hooks/useWebSocket'
import {
  applyMessageUpdate,
  canChangeMessage,
  isDeletedMessage,
  isEditedMessage,
} from './messageEdits'

function msg(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    type: 'message', id: 'm1', room_id: 'r1', participant_id: 'p1',
    content: 'hello', seq: 1, created_at: '2026-10-12T00:00:00Z',
    ...overrides,
  }
}

describe('messageEdits', () => {
  it('[MSG-08] reads edited and deleted markers from metadata', () => {
    expect(isEditedMessage(msg())).toBe(false)
    expect(isEditedMessage(msg({ metadata: { edited_at: '2026-10-12T01:00:00Z' } }))).toBe(true)
    expect(isDeletedMessage(msg({ metadata: { deleted_at: '2026-10-12T01:00:00Z' } }))).toBe(true)
    // A deleted message is shown as a placeholder, never as "(edited)".
    expect(isEditedMessage(msg({ metadata: { edited_at: 'x', deleted_at: 'y' } }))).toBe(false)
  })

  it('[MSG-08] only own plain human messages can be changed', () => {
    expect(canChangeMessage(msg(), { isMine: true, isAgent: false })).toBe(true)
    expect(canChangeMessage(msg(), { isMine: false, isAgent: false })).toBe(false)
    expect(canChangeMessage(msg(), { isMine: true, isAgent: true })).toBe(false)
    expect(canChangeMessage(msg({ metadata: { deleted_at: 'x' } }), { isMine: true, isAgent: false })).toBe(false)
    for (const key of ['system_origin', 'ingest_only', 'interaction', 'interaction_resolution', 'task_assignment', 'room_query']) {
      expect(canChangeMessage(msg({ metadata: { [key]: 'x' } }), { isMine: true, isAgent: false })).toBe(false)
    }
  })

  it('[MSG-08] replaces the matching message in place without moving seq order', () => {
    const list = [msg({ id: 'a', seq: 1 }), msg({ id: 'b', seq: 2, content: 'old' }), msg({ id: 'c', seq: 3 })]
    const next = applyMessageUpdate(list, msg({ id: 'b', seq: 2, content: 'new', type: 'message_updated' }))
    expect(next.map(m => m.id)).toEqual(['a', 'b', 'c'])
    expect(next[1].content).toBe('new')
    expect(next[1].type).toBe('message')
    expect(next[0]).toBe(list[0])
  })

  it('[MSG-08] ignores updates for unknown messages', () => {
    const list = [msg({ id: 'a' })]
    expect(applyMessageUpdate(list, msg({ id: 'zzz' }))).toBe(list)
  })
})

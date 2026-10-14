import { describe, it, expect } from 'vitest'
import { alignsRight } from './messageLayout'
import type { ChatMessage } from '@/hooks/useWebSocket'

function makeMessage(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    type: 'message',
    id: 'm1',
    room_id: 'r1',
    participant_id: 'me',
    content: 'hello',
    seq: 1,
    created_at: '2026-04-26T00:00:00Z',
    ...overrides,
  }
}

describe('alignsRight', () => {
  it('puts my own plain message on the right', () => {
    expect(alignsRight(makeMessage(), true)).toBe(true)
  })

  it("keeps someone else's message on the left", () => {
    expect(alignsRight(makeMessage(), false)).toBe(false)
  })

  it('keeps a task-assignment card I triggered on the left', () => {
    const msg = makeMessage({
      metadata: {
        task_assignment: { task_id: 't1', assignee_pid: 'p1', event: 'reassigned' },
      },
    })
    expect(alignsRight(msg, true)).toBe(false)
  })
})

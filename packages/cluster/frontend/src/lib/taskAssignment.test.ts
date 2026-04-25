import { describe, it, expect } from 'vitest'
import {
  parseTaskAssignment,
  stripTaskMentionPrefix,
} from './taskAssignment'
import type { ChatMessage } from '@/hooks/useWebSocket'

function makeMessage(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    type: 'message',
    id: 'm1',
    room_id: 'r1',
    participant_id: null,
    content: '',
    seq: 1,
    created_at: '2026-04-26T00:00:00Z',
    ...overrides,
  }
}

describe('stripTaskMentionPrefix', () => {
  it('strips mention token + [TASK] marker on a single-line content', () => {
    expect(stripTaskMentionPrefix('<@user:abc> [TASK] design review'))
      .toBe('design review')
  })

  it('keeps only the first line when the content is multi-line (#275)', () => {
    // The injection helper now embeds a self-instruction beneath the
    // canonical first line. The card must render only the first line as
    // its title — the trailing instruction is for the LLM, not for
    // human readers of the chat stream.
    const content =
      '<@user:abc> [TASK] design review\n' +
      '\n' +
      '_(이 task는 당신에게 배정되었습니다. ' +
      '시작 시 `mark_task_status(task_id="t1", status="in_progress")` 를 호출하고, ' +
      '완료되면 `status="done"` 으로 다시 호출하세요. ' +
      '차단되면 `status="blocked"`.)_'
    expect(stripTaskMentionPrefix(content)).toBe('design review')
  })

  it('handles content without [TASK] marker gracefully', () => {
    expect(stripTaskMentionPrefix('<@user:abc> bare title'))
      .toBe('bare title')
  })
})

describe('parseTaskAssignment', () => {
  it('returns null when metadata has no task_assignment block', () => {
    expect(parseTaskAssignment(makeMessage({ metadata: undefined }))).toBeNull()
    expect(parseTaskAssignment(makeMessage({ metadata: { foo: 1 } }))).toBeNull()
  })

  it('returns the parsed payload on a well-formed metadata', () => {
    const parsed = parseTaskAssignment(
      makeMessage({
        metadata: {
          task_assignment: {
            task_id: 't1',
            assignee_pid: 'p1',
            event: 'assigned',
          },
        },
      }),
    )
    expect(parsed).toEqual({
      task_id: 't1',
      assignee_pid: 'p1',
      event: 'assigned',
    })
  })

  it('rejects unknown event values', () => {
    const parsed = parseTaskAssignment(
      makeMessage({
        metadata: {
          task_assignment: {
            task_id: 't1',
            assignee_pid: 'p1',
            event: 'exploded',
          },
        },
      }),
    )
    expect(parsed).toBeNull()
  })
})

import { describe, it, expect } from 'vitest'
import {
  parseHandoff,
  stripHandoffPrefix,
  stripHandoffToTrailer,
  isHandoffStatusMessage,
} from './handoff'
import type { ChatMessage } from '@/hooks/useWebSocket'

function msg(extra: Partial<ChatMessage> = {}): ChatMessage {
  return {
    type: 'message',
    id: 'm1',
    room_id: 'room-a',
    participant_id: 'orch',
    content: '',
    seq: 1,
    created_at: new Date().toISOString(),
    ...extra,
  }
}

describe('parseHandoff', () => {
  it('returns metadata when content has [HANDOFF] prefix, mentions target user, and next_speaker set', () => {
    const m = msg({
      content: '[HANDOFF] <@user:target-pid> Round 1 자기소개를 부탁드립니다.',
      metadata: {
        next_speaker_participant_id: 'target-pid',
        mentions: [{ type: 'user', id: 'target-pid' }],
      },
    })
    expect(parseHandoff(m)).toEqual({
      targetParticipantId: 'target-pid',
      instruction: 'Round 1 자기소개를 부탁드립니다.',
      nextSpeakerParticipantId: 'target-pid',
    })
  })

  it('returns null when content is missing the [HANDOFF] prefix', () => {
    const m = msg({
      content: '마이크 넘기겠습니다 🎤',
      metadata: {
        next_speaker_participant_id: 'target-pid',
        mentions: [{ type: 'user', id: 'target-pid' }],
      },
    })
    expect(parseHandoff(m)).toBeNull()
  })

  it('returns null when next_speaker_participant_id is absent from metadata', () => {
    const m = msg({
      content: '[HANDOFF] <@user:target-pid> 계속해주세요',
      metadata: {
        mentions: [{ type: 'user', id: 'target-pid' }],
      },
    })
    expect(parseHandoff(m)).toBeNull()
  })

  it('returns null when the user mention is missing', () => {
    const m = msg({
      content: '[HANDOFF] 계속해주세요',
      metadata: {
        next_speaker_participant_id: 'target-pid',
        mentions: [{ type: 'agent', id: 'agent-x' }],
      },
    })
    expect(parseHandoff(m)).toBeNull()
  })

  it('returns null for non-handoff bracketed prefixes', () => {
    const m = msg({
      content: '[ROOM_QUERY] ping',
      metadata: {
        next_speaker_participant_id: 'target-pid',
        mentions: [{ type: 'user', id: 'target-pid' }],
      },
    })
    expect(parseHandoff(m)).toBeNull()
  })

  it('strips the user mention token from the instruction body', () => {
    const m = msg({
      content: '[HANDOFF]   <@user:target-pid>   please reply',
      metadata: {
        next_speaker_participant_id: 'target-pid',
        mentions: [{ type: 'user', id: 'target-pid' }],
      },
    })
    const parsed = parseHandoff(m)
    expect(parsed?.instruction).toBe('please reply')
  })

  it('returns null for empty metadata', () => {
    const m = msg({ content: '[HANDOFF] <@user:x> hi' })
    expect(parseHandoff(m)).toBeNull()
  })
})

describe('stripHandoffPrefix', () => {
  it('removes [HANDOFF] prefix and trailing whitespace', () => {
    expect(stripHandoffPrefix('[HANDOFF] hi')).toBe('hi')
    expect(stripHandoffPrefix('[HANDOFF]   hi')).toBe('hi')
  })

  it('leaves content without the prefix untouched', () => {
    expect(stripHandoffPrefix('hi')).toBe('hi')
  })
})

describe('stripHandoffToTrailer', () => {
  it('strips a trailing handoff_to: <@user:id> line', () => {
    const body = '답변 본문입니다.\n\nhandoff_to: <@user:target-pid>'
    expect(stripHandoffToTrailer(body)).toBe('답변 본문입니다.')
  })

  it('strips a trailing handoff_to: with participant_id: annotation', () => {
    const body =
      '답변 본문\n\nhandoff_to: Gemini CLI participant_id: abc-123'
    expect(stripHandoffToTrailer(body)).toBe('답변 본문')
  })

  it('strips a Korean "마이크 넘기겠습니다" trailer line', () => {
    const body = '끝났습니다.\n\nGemini CLI, 마이크 넘기겠습니다.'
    expect(stripHandoffToTrailer(body)).toBe('끝났습니다.')
  })

  it('leaves content without a trailer untouched', () => {
    expect(stripHandoffToTrailer('그냥 답변입니다.')).toBe('그냥 답변입니다.')
  })

  it('does not strip handoff_to when it appears mid-body', () => {
    // R2 — the regex must require an end-of-string anchor so an
    // occurrence of "handoff_to:" earlier in the body is preserved.
    const body =
      'handoff_to: inline mention here\n\n실제 답변 본문 뒤에 더 이어집니다.'
    expect(stripHandoffToTrailer(body)).toBe(body)
  })

  it('handles case-insensitive Handoff_To trailer', () => {
    const body = '답변\n\nHandoff_To: <@user:xyz>'
    expect(stripHandoffToTrailer(body)).toBe('답변')
  })
})

describe('isHandoffStatusMessage', () => {
  it('matches "에게 마이크를 넘겼습니다" status', () => {
    expect(isHandoffStatusMessage('Codex에게 마이크를 넘겼습니다. 🎤')).toBe(true)
  })

  it('matches "기다리고 있습니다..." status', () => {
    expect(isHandoffStatusMessage('응답을 기다리고 있습니다... 🎤')).toBe(true)
  })

  it('matches "전달하겠습니다" status', () => {
    expect(isHandoffStatusMessage('Gemini CLI에게 전달하겠습니다.')).toBe(true)
  })

  it('does not match ordinary messages', () => {
    expect(isHandoffStatusMessage('안녕하세요. 오늘의 진행 상황은…')).toBe(false)
    expect(isHandoffStatusMessage('[HANDOFF] <@user:x> go')).toBe(false)
  })

  it('does not match empty strings', () => {
    expect(isHandoffStatusMessage('')).toBe(false)
  })
})

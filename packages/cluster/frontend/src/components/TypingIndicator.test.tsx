// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import TypingIndicator from './TypingIndicator'

const participants = {
  agent: { id: 'agent', kind: 'agent', display_name: 'Codex' },
  agent2: { id: 'agent2', kind: 'agent', display_name: 'Pi' },
  human: { id: 'human', kind: 'user', display_name: 'Mina' },
} as Parameters<typeof TypingIndicator>[0]['participants']

describe('TypingIndicator', () => {
  it('shows the observed agent stage beside ordinary human typing', () => {
    render(<TypingIndicator typingUsers={new Set(['agent', 'human'])}
      typingStages={{ agent: 'using_tool' }} participants={participants} myParticipantId={null} />)
    expect(screen.getByText('Codex · 도구 실행 중…, Mina is typing…')).toBeTruthy()
  })

  it('keeps the previous wording when no stage exists', () => {
    render(<TypingIndicator typingUsers={new Set(['human'])}
      participants={participants} myParticipantId={null} />)
    expect(screen.getByText('Mina is typing…')).toBeTruthy()
  })

  it('shows separate stages for concurrent agents', () => {
    render(<TypingIndicator typingUsers={new Set(['agent', 'agent2'])}
      typingStages={{ agent: 'writing', agent2: 'preparing' }}
      participants={participants} myParticipantId={null} />)
    expect(screen.getByText('Codex · 응답 작성 중…, Pi · 응답 준비 중…')).toBeTruthy()
  })
})

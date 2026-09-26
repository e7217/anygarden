// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import TypingIndicator from './TypingIndicator'
import { LocaleProvider, type Locale } from '@/i18n/LocaleProvider'

const participants = {
  agent: { id: 'agent', kind: 'agent', display_name: 'Codex' },
  agent2: { id: 'agent2', kind: 'agent', display_name: 'Pi' },
  human: { id: 'human', kind: 'user', display_name: 'Mina' },
} as Parameters<typeof TypingIndicator>[0]['participants']

function renderInLocale(locale: Locale, ui: Parameters<typeof render>[0]) {
  localStorage.setItem('anygarden_locale', locale)
  return render(<LocaleProvider>{ui}</LocaleProvider>)
}

describe('TypingIndicator', () => {
  it('shows the observed agent stage beside ordinary human typing', () => {
    renderInLocale('ko', <TypingIndicator typingUsers={new Set(['agent', 'human'])}
      typingStages={{ agent: 'using_tool' }} participants={participants} myParticipantId={null} />)
    expect(screen.getByText('Codex · 도구 실행 중…, Mina 입력 중…')).toBeTruthy()
  })

  it('keeps the previous wording when no stage exists', () => {
    renderInLocale('en', <TypingIndicator typingUsers={new Set(['human'])}
      participants={participants} myParticipantId={null} />)
    expect(screen.getByText('Mina is typing…')).toBeTruthy()
  })

  it('shows separate stages for concurrent agents', () => {
    renderInLocale('ko', <TypingIndicator typingUsers={new Set(['agent', 'agent2'])}
      typingStages={{ agent: 'writing', agent2: 'preparing' }}
      participants={participants} myParticipantId={null} />)
    expect(screen.getByText('Codex · 응답 작성 중…, Pi · 응답 준비 중…')).toBeTruthy()
  })
})

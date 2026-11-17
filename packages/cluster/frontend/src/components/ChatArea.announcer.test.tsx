// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import ChatArea from './ChatArea'
import { LocaleProvider } from '@/i18n/LocaleProvider'
import type { ChatMessage } from '@/hooks/useWebSocket'
import type { Participant } from '@/pages/ChatPage'

vi.mock('@/hooks/useRooms', () => ({ useRooms: () => ({ rooms: {}, agentDMs: [] }) }))
vi.mock('@/hooks/useRoomFiles', () => ({ useRoomFiles: () => ({ files: [] }) }))
vi.mock('@/components/MessageBubble', () => ({
  default: ({ message }: { message: { content: string } }) => <div>{message.content}</div>,
}))
vi.mock('@/components/RoomQueryBanner', () => ({ default: () => null }))
vi.mock('@/components/EntityAvatar', () => ({ EntityAvatar: () => null }))
vi.mock('@/components/BrailleSpinner', () => ({ default: () => null }))

beforeAll(() => { Element.prototype.scrollIntoView = vi.fn() })
beforeEach(() => localStorage.setItem('anygarden_locale', 'en'))
afterEach(() => { cleanup(); localStorage.clear() })

const participants: Record<string, Participant> = {
  me: { id: 'me', display_name: 'Me', kind: 'user' },
  dev: { id: 'dev', display_name: 'dev', kind: 'agent' },
}

function msg(seq: number, participant: string, content: string, room = 'r1'): ChatMessage {
  return {
    type: 'message', id: `${room}-m${seq}`, room_id: room, participant_id: participant,
    content, seq, created_at: '2026-11-17T00:00:00Z',
  }
}

function area(messages: ChatMessage[], extra: Partial<Parameters<typeof ChatArea>[0]> = {}) {
  return (
    <LocaleProvider>
      <ChatArea messages={messages} participants={participants} myParticipantId="me" {...extra} />
    </LocaleProvider>
  )
}

const announcer = () => screen.getByTestId('chat-announcer')

describe('ChatArea screen-reader announcements', () => {
  it('stays silent for the history loaded on entry', () => {
    render(area([msg(1, 'dev', 'old answer'), msg(2, 'me', 'old question')]))
    expect(announcer()).toHaveAttribute('aria-live', 'polite')
    expect(announcer()).toHaveTextContent('')
  })

  it('announces a new message from someone else with its sender and an excerpt', () => {
    const history = [msg(1, 'me', 'go')]
    const { rerender } = render(area(history))
    rerender(area([...history, msg(2, 'dev', '**Done.** Ask <@user:me> to review')]))
    expect(announcer()).toHaveTextContent('dev: Done. Ask @Me to review')
  })

  it('does not announce one\'s own messages or older pages loaded above', () => {
    const history = [msg(5, 'dev', 'latest')]
    const { rerender } = render(area(history))
    rerender(area([msg(3, 'dev', 'older page'), ...history]))
    rerender(area([msg(3, 'dev', 'older page'), ...history, msg(6, 'me', 'thanks')]))
    expect(announcer()).toHaveTextContent('')
  })

  it('treats the first snapshot of another room as history', () => {
    const { rerender } = render(area([msg(1, 'dev', 'room one')]))
    rerender(area([]))
    rerender(area([msg(1, 'dev', 'room two history', 'r2'), msg(2, 'dev', 'more history', 'r2')]))
    expect(announcer()).toHaveTextContent('')
  })

  it('announces who is working, without the ticking timers', () => {
    render(area([msg(1, 'me', 'go')], { typingUsers: new Set(['dev']), typingSince: { dev: Date.now() } }))
    expect(screen.getByTestId('chat-working-announcer')).toHaveTextContent(/^dev working$/)
  })
})

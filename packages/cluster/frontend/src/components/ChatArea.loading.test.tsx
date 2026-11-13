// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import ChatArea from './ChatArea'
import { LocaleProvider } from '@/i18n/LocaleProvider'

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

const message = {
  type: 'message' as const, id: 'm1', room_id: 'r1', participant_id: 'me',
  content: 'hello', seq: 1, created_at: '2026-11-13T00:00:00Z',
}

function renderArea(props: Partial<Parameters<typeof ChatArea>[0]>) {
  return render(
    <LocaleProvider>
      <ChatArea messages={[]} participants={{}} myParticipantId="me" {...props} />
    </LocaleProvider>,
  )
}

describe('ChatArea history loading', () => {
  it('shows a loading placeholder, not the empty-room prompt, while history loads', () => {
    renderArea({ loading: true })
    expect(screen.getByRole('status')).toHaveTextContent('Loading messages…')
    expect(screen.queryByText('No messages yet')).not.toBeInTheDocument()
  })

  it('shows the empty-room prompt once loading finished with no messages', () => {
    renderArea({ loading: false })
    expect(screen.getByText('No messages yet')).toBeInTheDocument()
    expect(screen.queryByTestId('chat-history-loading')).not.toBeInTheDocument()
  })

  it('renders messages that arrived before history finished', () => {
    renderArea({ loading: true, messages: [message] })
    expect(screen.getByText('hello')).toBeInTheDocument()
    expect(screen.queryByTestId('chat-history-loading')).not.toBeInTheDocument()
  })
})

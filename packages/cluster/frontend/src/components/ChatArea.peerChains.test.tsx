// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import ChatArea from './ChatArea'
import { LocaleProvider } from '@/i18n/LocaleProvider'
import { indexThreads } from '@/lib/threads'
import { sampleChain } from '@/lib/peerChains.fixtures'

vi.mock('@/hooks/useRooms', () => ({ useRooms: () => ({ rooms: {}, agentDMs: [] }) }))
vi.mock('@/hooks/useRoomFiles', () => ({ useRoomFiles: () => ({ files: [] }) }))
vi.mock('@/components/MessageBubble', () => ({
  default: ({ message }: { message: { content: string } }) => <div>{message.content}</div>,
}))
vi.mock('@/components/RoomQueryBanner', () => ({ default: () => null }))
vi.mock('@/components/EntityAvatar', () => ({ EntityAvatar: () => null }))

beforeAll(() => { Element.prototype.scrollIntoView = vi.fn() })
beforeEach(() => localStorage.setItem('anygarden_locale', 'en'))
afterEach(() => { cleanup(); localStorage.clear() })

const messages = [
  { type: 'message', id: 'human-1', room_id: 'room-1', participant_id: 'me', content: 'check it', seq: 1, created_at: '2026-11-10T00:00:00Z' },
  { type: 'message', id: 'other', room_id: 'room-1', participant_id: 'me', content: 'unrelated', seq: 2, created_at: '2026-11-10T00:00:01Z' },
]

function renderArea(extra: Partial<Parameters<typeof ChatArea>[0]> = {}) {
  return render(
    <LocaleProvider>
      <ChatArea
        messages={messages}
        participants={{}}
        myParticipantId="me"
        threadIndex={indexThreads(messages)}
        onOpenThread={vi.fn()}
        {...extra}
      />
    </LocaleProvider>,
  )
}

describe('ChatArea question chain progress', () => {
  it('[COL-17] puts the card under the message the chain hangs off, and opens its thread', () => {
    const onOpenThread = vi.fn()
    renderArea({ peerChains: [sampleChain()], onOpenThread })
    const card = screen.getByTestId('peer-chain-progress')
    expect(card.closest('[data-message-id]')).toHaveAttribute('data-message-id', 'human-1')
    fireEvent.click(screen.getByRole('button', { name: 'Open thread' }))
    expect(onOpenThread).toHaveBeenCalledWith('human-1')
  })

  it('does not toggle an already open thread closed', () => {
    const onOpenThread = vi.fn()
    renderArea({ peerChains: [sampleChain()], onOpenThread, activeThreadRootId: 'human-1' })
    fireEvent.click(screen.getByRole('button', { name: 'Open thread' }))
    expect(onOpenThread).not.toHaveBeenCalled()
  })

  it('shows no card for a finished chain', () => {
    renderArea({ peerChains: [sampleChain({ state: 'done' })] })
    expect(screen.queryByTestId('peer-chain-progress')).toBeNull()
  })

  it('offers stop only when the viewer may cancel turns', () => {
    const { unmount } = renderArea({ peerChains: [sampleChain()], onCancelPeerChain: vi.fn() })
    expect(screen.getByRole('button', { name: 'Stop every agent in this question chain' })).toBeInTheDocument()
    unmount()
    renderArea({ peerChains: [sampleChain()], onCancelPeerChain: vi.fn(), canCancelTurns: false })
    expect(screen.queryByRole('button', { name: 'Stop every agent in this question chain' })).toBeNull()
  })
})

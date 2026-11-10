// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import ChatArea from './ChatArea'
import { LocaleProvider } from '@/i18n/LocaleProvider'
import { indexThreads } from '@/lib/threads'
import { sampleChain } from '@/lib/peerChains.fixtures'
import type { OpenTurn } from '@/lib/openTurns'

vi.mock('@/hooks/useRooms', () => ({ useRooms: () => ({ rooms: {}, agentDMs: [] }) }))
vi.mock('@/hooks/useRoomFiles', () => ({ useRoomFiles: () => ({ files: [] }) }))
vi.mock('@/components/MessageBubble', () => ({
  default: ({ message }: { message: { content: string } }) => <div>{message.content}</div>,
}))
vi.mock('@/components/RoomQueryBanner', () => ({ default: () => null }))
vi.mock('@/components/EntityAvatar', () => ({ EntityAvatar: () => null }))

beforeAll(() => { Element.prototype.scrollIntoView = vi.fn() })
beforeEach(() => localStorage.setItem('anygarden_locale', 'en'))
afterEach(() => { cleanup(); localStorage.clear(); vi.restoreAllMocks() })

async function flush() {
  await act(async () => { for (let i = 0; i < 5; i++) await Promise.resolve() })
}

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
  const STOP = 'Stop every agent in this thread and its question chain'

  it('[COL-17] reports a running chain on the thread reply row, with no separate card', () => {
    const onOpenThread = vi.fn()
    renderArea({ peerChains: [sampleChain()], onOpenThread })
    expect(screen.queryByTestId('peer-chain-progress')).toBeNull()
    // No reply has landed yet, but the row shows the work instead of the
    // quiet "Reply in thread" offer.
    expect(screen.getByTestId('thread-working')).toHaveTextContent('G answering')
    const row = document.querySelector('[data-thread-trigger="human-1"]') as HTMLElement
    expect(row.closest('[data-message-id]')).toHaveAttribute('data-message-id', 'human-1')
    fireEvent.click(row)
    expect(onOpenThread).toHaveBeenCalledWith('human-1')
  })

  it('[COL-17] describes who asked whom for hover and assistive tech', () => {
    renderArea({ peerChains: [sampleChain()] })
    const summary = screen.getByTestId('peer-chain-hover')
    expect(document.querySelector('[data-thread-trigger="human-1"]')).toHaveAttribute('aria-describedby', summary.id)
    expect(summary).toHaveTextContent('F → G')
    expect(summary.querySelector('[data-node-id="G"] [data-state]')).toHaveAttribute('data-state', 'answering')
  })

  it('[COL-17] has one stop that ends every chain and the thread\'s other work, after confirmation', async () => {
    const onCancelPeerChain = vi.fn().mockResolvedValue(true)
    const onCancelTurns = vi.fn().mockResolvedValue(true)
    const openTurns: OpenTurn[] = [{ request_id: 'turn-x', participant_id: 'px', thread_root_id: 'human-1', state: 'answering' }]
    vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true)
    renderArea({
      peerChains: [sampleChain(), sampleChain({ id: 'chain-2', started_at: '2026-11-09T00:00:00Z' })],
      onCancelPeerChain,
      onCancelTurns,
      openTurns,
    })
    const stops = screen.getAllByRole('button', { name: /^Stop/ })
    expect(stops).toHaveLength(1)
    expect(stops[0]).toHaveAccessibleName(STOP)
    expect(stops[0]).toHaveTextContent('Stop')

    fireEvent.click(stops[0])
    await flush()
    expect(onCancelPeerChain).not.toHaveBeenCalled()

    fireEvent.click(stops[0])
    await flush()
    expect(onCancelPeerChain.mock.calls.map(call => call[0]).sort()).toEqual(['chain-1', 'chain-2'])
    expect(onCancelTurns).toHaveBeenCalledWith(['turn-x'])
  })

  it('falls back to the card where the message has no reply row', () => {
    renderArea({ peerChains: [sampleChain()], threadIndex: undefined, onOpenThread: undefined })
    const card = screen.getByTestId('peer-chain-progress')
    expect(card.closest('[data-message-id]')).toHaveAttribute('data-message-id', 'human-1')
    expect(card).not.toHaveTextContent('depth')
  })

  it('shows neither progress nor card for a finished chain', () => {
    renderArea({ peerChains: [sampleChain({ state: 'done' })] })
    expect(screen.queryByTestId('peer-chain-progress')).toBeNull()
    expect(screen.queryByTestId('thread-working')).toBeNull()
  })

  it('offers stop only when the viewer may cancel turns', () => {
    const { unmount } = renderArea({ peerChains: [sampleChain()], onCancelPeerChain: vi.fn() })
    expect(screen.getByRole('button', { name: STOP })).toBeInTheDocument()
    unmount()
    renderArea({ peerChains: [sampleChain()], onCancelPeerChain: vi.fn(), canCancelTurns: false })
    expect(screen.queryByRole('button', { name: STOP })).toBeNull()
  })
})

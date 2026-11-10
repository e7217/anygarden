// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import ThreadPanel from './ThreadPanel'
import { LocaleProvider } from '@/i18n/LocaleProvider'
import { sampleChain } from '@/lib/peerChains.fixtures'
import type { ChatMessage } from '@/hooks/useWebSocket'

vi.mock('@/hooks/useRoomFiles', () => ({ useRoomFiles: () => ({ files: [] }) }))
vi.mock('@/hooks/useRightSidebarLayout', () => ({ useRightSidebarLayout: () => ({ width: 320 }) }))
vi.mock('@/components/right-rail/RailResizeHandle', () => ({ default: () => null }))
vi.mock('@/components/MessageInput', () => ({ default: () => null }))
vi.mock('@/components/peer-chains/PeerChainGraph', () => ({ default: () => null }))
vi.mock('@/components/MessageBubble', () => ({
  default: ({ message }: { message: ChatMessage }) => <div data-testid="bubble">{message.content}</div>,
}))

beforeAll(() => { Element.prototype.scrollIntoView = vi.fn() })
beforeEach(() => localStorage.setItem('anygarden_locale', 'en'))
afterEach(() => { cleanup(); localStorage.clear() })

let seq = 0
function msg(id: string, content = id): ChatMessage {
  seq += 1
  return {
    type: 'message', id, room_id: 'room-1', participant_id: 'p', content,
    parent_message_id: 'human-1', root_message_id: 'human-1', seq, created_at: '2026-11-10T00:00:00Z',
  }
}

function renderPanel(chains = [sampleChain()]) {
  const replies = ['q-B', 'q-C', 'r-C', 'r-B', 'q-D', 'q-E', 'q-F', 'q-G', 'plain'].map(id => msg(id))
  return render(
    <LocaleProvider>
      <ThreadPanel
        root={{ ...msg('human-1', 'please check'), parent_message_id: null, root_message_id: null }}
        replies={replies}
        participants={{}}
        myParticipantId={null}
        roomId="room-1"
        connected
        onSend={vi.fn()}
        onTyping={vi.fn()}
        onClose={vi.fn()}
        peerChains={chains}
      />
    </LocaleProvider>,
  )
}

const bubbles = () => screen.getAllByTestId('bubble').map(el => el.textContent)

describe('ThreadPanel with a question chain', () => {
  it('[COL-17] shows the call tree and labels question/answer messages', () => {
    renderPanel()
    expect(screen.getByTestId('peer-chain-tree')).toBeInTheDocument()
    const labels = screen.getAllByTestId('peer-message-label').map(el => el.textContent)
    expect(labels).toEqual(['A → B', 'B → C', 'C → B', 'B → A', 'A → D', 'D → E', 'E → F', '… › E › F → G'])
  })

  it('filters the thread to a branch and shows everything again on reset', () => {
    renderPanel()
    fireEvent.click(screen.getByRole('button', { name: /^Show only messages of A → B/ }))
    expect(bubbles()).toEqual(['please check', 'q-B', 'q-C', 'r-C', 'r-B'])
    expect(screen.getByTestId('peer-branch-filter')).toHaveTextContent('Showing only A → B and the questions below it')

    fireEvent.click(screen.getByRole('button', { name: 'Show all' }))
    expect(bubbles()).toHaveLength(10)
    expect(screen.queryByTestId('peer-branch-filter')).toBeNull()
  })

  it('stays a plain thread when no chain belongs to it', () => {
    renderPanel([sampleChain({ thread_root_id: 'human-2', origin_message_id: 'human-2' })])
    expect(screen.queryByTestId('peer-chain-tree')).toBeNull()
    expect(screen.queryAllByTestId('peer-message-label')).toHaveLength(0)
  })
})

// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import ChatArea from './ChatArea'
import { indexThreads } from '@/lib/threads'
import type { ChatMessage } from '@/hooks/useWebSocket'
import type { Participant } from '@/pages/ChatPage'

// These assertions are about which thread controls ChatArea attaches to
// which rows — not about how a bubble renders. Stubbing the heavy children
// keeps the test on that question and off their dependency graphs.
vi.mock('@/hooks/useRooms', () => ({
  useRooms: () => ({ rooms: {}, agentDMs: [] }),
}))
vi.mock('@/hooks/useRoomFiles', () => ({
  useRoomFiles: () => ({ files: [] }),
}))
vi.mock('@/components/MessageBubble', () => ({
  default: ({ message }: { message: { content: string } }) => (
    <div>{message.content}</div>
  ),
}))
vi.mock('@/components/RoomQueryBanner', () => ({ default: () => null }))
// Stub EntityAvatar to avoid pulling in @lobehub/ui transitively — same
// reason as Sidebar.test.tsx. These assertions read the affordance's
// accessible name, not its avatar cluster.
vi.mock('@/components/EntityAvatar', () => ({ EntityAvatar: () => null }))
vi.mock('@/components/BrailleSpinner', () => ({ default: () => null }))

// jsdom has no layout engine, so Element.scrollIntoView is undefined.
// ChatArea calls it to follow new messages.
beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn()
})

afterEach(() => cleanup())

function msg(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    type: 'message',
    id: 'm1',
    room_id: 'r1',
    participant_id: 'p1',
    content: 'body',
    seq: 1,
    created_at: '2026-08-09T00:00:00Z',
    ...overrides,
  }
}

function reply(rootId: string, overrides: Partial<ChatMessage> = {}): ChatMessage {
  return msg({ parent_message_id: rootId, root_message_id: rootId, ...overrides })
}

const participants: Record<string, Participant> = {
  p1: { id: 'p1', display_name: 'Alice', kind: 'user' },
}

function renderArea(
  messages: ChatMessage[],
  extra: Partial<Parameters<typeof ChatArea>[0]> = {},
) {
  return render(
    <ChatArea
      messages={messages}
      participants={participants}
      myParticipantId="p1"
      threadIndex={indexThreads(messages)}
      onOpenThread={() => {}}
      {...extra}
    />,
  )
}

describe('ChatArea thread affordances', () => {
  it('offers a thread on a genuine top-level message', () => {
    renderArea([msg({ id: 'root', seq: 1, content: 'root body' })])

    expect(
      screen.getByRole('button', { name: /reply in thread/i }),
    ).toBeInTheDocument()
  })

  it('shows an orphaned reply but offers no thread on it', () => {
    // A reply whose root scrolled out of the loaded window is surfaced so
    // it isn't lost. It must not carry an affordance: the server rejects
    // a thread rooted at a reply, so the control would always fail.
    renderArea([reply('scrolled-away', { id: 'orphan', seq: 5, content: 'orphan body' })])

    expect(screen.getByText('orphan body')).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: /reply in thread/i }),
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: /open thread/i }),
    ).not.toBeInTheDocument()
  })

  it('keeps the affordance on a real root while withholding it from an orphan beside it', () => {
    renderArea([
      msg({ id: 'root', seq: 1, content: 'root body' }),
      reply('root', { id: 'r1', seq: 2, content: 'a reply' }),
      reply('scrolled-away', { id: 'orphan', seq: 3, content: 'orphan body' }),
    ])

    // Exactly one thread control: the real root's, reporting its reply.
    expect(
      screen.getByRole('button', { name: /open thread, 1 reply/i }),
    ).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: /reply in thread/i }),
    ).not.toBeInTheDocument()
    // The reply itself is hoisted into the thread, not the timeline.
    expect(screen.queryByText('a reply')).not.toBeInTheDocument()
    expect(screen.getByText('orphan body')).toBeInTheDocument()
  })

  it('does not expand an inline thread under an orphan', () => {
    // Same guard on the inline path: even if an orphan id somehow became
    // the active thread, no composer may open under it.
    const messages = [
      reply('scrolled-away', { id: 'orphan', seq: 5, content: 'orphan body' }),
    ]
    renderArea(messages, {
      activeThreadRootId: 'orphan',
      renderInlineThread: () => <div data-testid="inline-thread" />,
    })

    expect(screen.queryByTestId('inline-thread')).not.toBeInTheDocument()
  })

  it('expands an inline thread under a real root', () => {
    renderArea([msg({ id: 'root', seq: 1, content: 'root body' })], {
      activeThreadRootId: 'root',
      renderInlineThread: () => <div data-testid="inline-thread" />,
    })

    expect(screen.getByTestId('inline-thread')).toBeInTheDocument()
  })

  it('renders every message inline and offers no threads without an index', () => {
    // The guest room (§11.5) omits threadIndex — replies stay in the
    // timeline and no thread control appears.
    render(
      <ChatArea
        messages={[
          msg({ id: 'root', seq: 1, content: 'root body' }),
          reply('root', { id: 'r1', seq: 2, content: 'a reply' }),
        ]}
        participants={participants}
        myParticipantId="p1"
      />,
    )

    expect(screen.getByText('root body')).toBeInTheDocument()
    expect(screen.getByText('a reply')).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: /thread/i }),
    ).not.toBeInTheDocument()
  })
})

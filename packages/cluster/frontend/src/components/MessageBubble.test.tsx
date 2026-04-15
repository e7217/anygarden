// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import MessageBubble from './MessageBubble'
import type { ChatMessage } from '@/hooks/useWebSocket'
import type { Participant } from '@/pages/ChatPage'

afterEach(() => cleanup())

// `useRooms` is a provider-backed hook; stub it for these
// presentational tests so we don't need to wire up the whole
// RoomsProvider. The two tests below only rely on ``rooms``
// being a plain object.
vi.mock('@/hooks/useRooms', () => ({
  useRooms: () => ({
    rooms: {
      'proj-a': [{ id: 'room-src', name: '설계팀', project_id: 'proj-a', is_dm: false }],
    },
  }),
}))

function baseMsg(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    type: 'message',
    id: 'm1',
    room_id: 'room-b',
    participant_id: 'agent-rep',
    content: 'hello',
    seq: 1,
    created_at: new Date().toISOString(),
    ...overrides,
  }
}

const participants: Record<string, Participant> = {
  'agent-rep': { id: 'agent-rep', display_name: 'Rep', kind: 'agent' },
  'user-alice': { id: 'user-alice', display_name: 'Alice', kind: 'user' },
  'agent-1': { id: 'agent-1', display_name: 'Helper', kind: 'agent' },
}

describe('MessageBubble — room_query forward variant', () => {
  it('renders source badge, strips [ROOM_QUERY] prefix, keeps wire body intact', () => {
    const msg = baseMsg({
      content: '[ROOM_QUERY] 배포 언제?',
      metadata: {
        room_query_forward: {
          query_id: 'q1',
          source_room_id: 'room-src',
          source_participant_id: 'user-alice',
        },
      },
    })
    render(
      <MessageBubble message={msg} participants={participants} isMine={false} />,
    )
    const forward = screen.getByTestId('room-query-forward')
    expect(forward).toBeInTheDocument()
    const badge = screen.getByTestId('room-query-forward-badge')
    expect(badge).toHaveTextContent('#설계팀')
    expect(badge).toHaveTextContent('@Alice')
    // Rendered body has no [ROOM_QUERY] prefix
    expect(forward).toHaveTextContent('배포 언제?')
    expect(forward).not.toHaveTextContent('[ROOM_QUERY]')
    // Wire body on the message is still prefixed — backend
    // ``should_respond`` depends on startswith.
    expect(msg.content).toBe('[ROOM_QUERY] 배포 언제?')
  })

  it('falls back to id slices when source room and user cannot be resolved', () => {
    const msg = baseMsg({
      content: '[ROOM_QUERY] test',
      metadata: {
        room_query_forward: {
          query_id: 'q1',
          source_room_id: 'unknown-room-11abcd',
          source_participant_id: 'unknown-pid-xyzzzz',
        },
      },
    })
    render(
      <MessageBubble message={msg} participants={participants} isMine={false} />,
    )
    const badge = screen.getByTestId('room-query-forward-badge')
    // Last 6 chars of ids
    expect(badge).toHaveTextContent('#11abcd')
    expect(badge).toHaveTextContent('@xyzzzz')
  })
})

describe('MessageBubble — room_query result variant', () => {
  it('delegates to RoomQueryResultCard with participant name map', () => {
    const msg = baseMsg({
      content: '[취합 결과] (1/1명 응답)\n\n질문: ...',
      metadata: {
        room_query_result: {
          query_id: 'q1',
          target_room_id: 'room-src',
          responded: 1,
          expected: 1,
          status: 'completed',
          responses: [{ participant_id: 'agent-1', content: '곧 배포합니다' }],
        },
      },
    })
    render(
      <MessageBubble message={msg} participants={participants} isMine={false} />,
    )
    const card = screen.getByTestId('room-query-result-q1')
    expect(card).toBeInTheDocument()
    // Name resolved from participants map
    expect(card).toHaveTextContent('@Helper')
    expect(card).toHaveTextContent('곧 배포합니다')
    // Room name resolved via useRooms stub
    expect(card).toHaveTextContent('#설계팀')
  })
})

describe('MessageBubble — plain regression', () => {
  it('renders a plain message untouched when no room_query metadata present', () => {
    const msg = baseMsg({ content: 'just a hello' })
    render(
      <MessageBubble message={msg} participants={participants} isMine={false} />,
    )
    expect(screen.queryByTestId('room-query-forward')).toBeNull()
    expect(screen.queryByTestId('room-query-result-q1')).toBeNull()
    expect(screen.getByText('just a hello')).toBeInTheDocument()
  })
})

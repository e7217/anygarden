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

// Stub EntityAvatar so these presentational tests don't pull in the
// @lobehub/icons bundle transitively. The avatar is already covered
// by its own unit tests; here we only care that MessageBubble wires
// it up with the right kind + id.
vi.mock('@/components/EntityAvatar', () => ({
  EntityAvatar: ({ id, name, kind, 'data-testid': testId }: {
    id: string
    name: string
    kind: string
    'data-testid'?: string
  }) => (
    <span
      data-testid={testId ?? 'entity-avatar'}
      data-id={id}
      data-kind={kind}
    >
      {name.slice(0, 2)}
    </span>
  ),
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

describe('MessageBubble — avatar wiring', () => {
  it('renders an agent-kind avatar for an agent sender', () => {
    const msg = baseMsg({
      id: 'ma',
      participant_id: 'agent-rep',
      content: 'hi from an agent',
    })
    render(
      <MessageBubble message={msg} participants={participants} isMine={false} />,
    )
    const avatar = screen.getByTestId('message-avatar')
    expect(avatar.getAttribute('data-kind')).toBe('agent')
    expect(avatar.getAttribute('data-id')).toBe('agent-rep')
  })

  it('renders a user-kind avatar for a regular user sender', () => {
    const msg = baseMsg({
      id: 'mu',
      participant_id: 'user-alice',
      content: 'hi',
    })
    render(
      <MessageBubble message={msg} participants={participants} isMine={false} />,
    )
    const avatar = screen.getByTestId('message-avatar')
    expect(avatar.getAttribute('data-kind')).toBe('user')
    expect(avatar.getAttribute('data-id')).toBe('user-alice')
  })

  it('still renders an avatar for orphan rows (participant_id=null)', () => {
    const msg = baseMsg({
      id: 'mo',
      participant_id: null as unknown as string,
      content: 'ghost message',
    })
    render(
      <MessageBubble message={msg} participants={participants} isMine={false} />,
    )
    const avatar = screen.getByTestId('message-avatar')
    // Orphans get a stable per-message seed so two orphans don't
    // accidentally share a color.
    expect(avatar.getAttribute('data-id')).toBe('orphan-mo')
    expect(avatar.getAttribute('data-kind')).toBe('user')
  })

  it('flags anonymous guests as kind=guest', () => {
    const guestParticipants: Record<string, Participant> = {
      'guest-1': {
        id: 'guest-1',
        display_name: 'Visitor',
        kind: 'user',
        is_anonymous: true,
      },
    }
    const msg = baseMsg({
      id: 'mg',
      participant_id: 'guest-1',
      content: 'hi',
    })
    render(
      <MessageBubble
        message={msg}
        participants={guestParticipants}
        isMine={false}
      />,
    )
    const avatar = screen.getByTestId('message-avatar')
    expect(avatar.getAttribute('data-kind')).toBe('guest')
  })
})

// Issue #94 — the origin question bubble must visibly indicate that a
// room_query is still in flight so the user can tie the banner's
// pending chip back to their own message.
describe('MessageBubble — question pending badge', () => {
  function questionMsg(content = '배포 언제?'): ChatMessage {
    return baseMsg({
      id: 'mq',
      room_id: 'room-src',
      participant_id: 'user-alice',
      content,
      metadata: {
        room_query: {
          role: 'question',
          query_id: 'q1',
          target_room_id: 'room-b',
          source_room_id: 'room-src',
          source_participant_id: 'user-alice',
        },
      },
    })
  }

  it('shows pending badge when query_id is in pendingQueryIds', () => {
    render(
      <MessageBubble
        message={questionMsg()}
        participants={participants}
        isMine={true}
        pendingQueryIds={new Set(['q1'])}
      />,
    )
    expect(screen.getByTestId('question-pending-badge')).toBeInTheDocument()
    expect(screen.getByTestId('question-pending-badge')).toHaveTextContent(
      '응답 대기 중',
    )
  })

  it('does not show badge when pendingQueryIds omits the query_id', () => {
    render(
      <MessageBubble
        message={questionMsg()}
        participants={participants}
        isMine={true}
        pendingQueryIds={new Set(['other'])}
      />,
    )
    expect(screen.queryByTestId('question-pending-badge')).toBeNull()
  })

  it('does not show badge when pendingQueryIds prop is omitted', () => {
    render(
      <MessageBubble
        message={questionMsg()}
        participants={participants}
        isMine={true}
      />,
    )
    expect(screen.queryByTestId('question-pending-badge')).toBeNull()
  })

  it('does not show badge for a non-question message even with matching id', () => {
    const msg = baseMsg({ content: 'plain reply' })
    render(
      <MessageBubble
        message={msg}
        participants={participants}
        isMine={false}
        pendingQueryIds={new Set(['q1'])}
      />,
    )
    expect(screen.queryByTestId('question-pending-badge')).toBeNull()
  })
})

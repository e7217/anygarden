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
// it up with the right kind + id + engine.
vi.mock('@/components/EntityAvatar', () => ({
  EntityAvatar: ({ id, name, kind, engine, 'data-testid': testId }: {
    id: string
    name: string
    kind: string
    engine?: string
    'data-testid'?: string
  }) => (
    <span
      data-testid={testId ?? 'entity-avatar'}
      data-id={id}
      data-kind={kind}
      data-engine={engine ?? ''}
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
  'agent-rep': {
    id: 'agent-rep',
    display_name: 'Rep',
    kind: 'agent',
    engine: 'claude-code',
  },
  'user-alice': { id: 'user-alice', display_name: 'Alice', kind: 'user' },
  'agent-1': {
    id: 'agent-1',
    display_name: 'Helper',
    kind: 'agent',
    engine: 'codex',
  },
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

  it('prefers source_participant_name over resolveUser when cross-room (issue #155)', () => {
    // #155 — source user is NOT in this (target) room's participants
    // map. Without a server-supplied snapshot the badge would render
    // the last 6 hex of the UUID. The server now ships a snapshot
    // ``source_participant_name`` so the badge renders the real name.
    const msg = baseMsg({
      content: '[ROOM_QUERY] cross-room?',
      metadata: {
        room_query_forward: {
          query_id: 'q1',
          source_room_id: 'room-src',
          source_participant_id: 'stranger-pid-123456',
          source_participant_name: 'Alice',
        },
      },
    })
    render(
      <MessageBubble message={msg} participants={participants} isMine={false} />,
    )
    const badge = screen.getByTestId('room-query-forward-badge')
    expect(badge).toHaveTextContent('#설계팀')
    expect(badge).toHaveTextContent('@Alice')
    // Must NOT fall through to the hash slice — that was the bug.
    expect(badge).not.toHaveTextContent('@123456')
  })

  it('falls back to resolveUser when server omits source_participant_name (pre-#155)', () => {
    // Pre-#155 servers don't ship ``source_participant_name``. If the
    // source user happens to also be in the target room (same-room
    // forwards, or a user joined both), ``resolveUser`` still works
    // and the legacy path renders their name.
    const msg = baseMsg({
      content: '[ROOM_QUERY] legacy',
      metadata: {
        room_query_forward: {
          query_id: 'q1',
          source_room_id: 'room-src',
          source_participant_id: 'user-alice',
          // no source_participant_name — legacy server payload
        },
      },
    })
    render(
      <MessageBubble message={msg} participants={participants} isMine={false} />,
    )
    const badge = screen.getByTestId('room-query-forward-badge')
    // user-alice IS in this room's participants map (see fixture) so
    // resolveUser succeeds → shows @Alice, not a hash slice.
    expect(badge).toHaveTextContent('@Alice')
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

  it('renders inline shared-file references distinctly from attachments', () => {
    const msg = baseMsg({
      content: 'please review',
      metadata: {
        references: [
          {
            type: 'shared_file',
            id: 'file-1',
            name: 'spec.md',
            storage_name: 'spec.md',
            origin: 'inline',
          },
          {
            type: 'shared_file',
            id: 'file-2',
            name: 'data.json',
            storage_name: 'data.json',
            origin: 'attachment',
          },
        ],
      },
    })
    render(
      <MessageBubble message={msg} participants={participants} isMine={false} />,
    )

    expect(screen.getByText('$ spec.md')).toBeInTheDocument()
    expect(screen.getByText('data.json')).toBeInTheDocument()
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

  it('forwards the agent engine to the avatar (#102)', () => {
    // The agent fixture carries engine='claude-code'; once Participant
    // exposes ``engine`` the bubble must pass it through so the avatar
    // renders the corner engine-mark badge for non-admin viewers.
    const msg = baseMsg({
      id: 'me',
      participant_id: 'agent-rep',
      content: 'engine check',
    })
    render(
      <MessageBubble message={msg} participants={participants} isMine={false} />,
    )
    const avatar = screen.getByTestId('message-avatar')
    expect(avatar.getAttribute('data-engine')).toBe('claude-code')
  })

  it('leaves data-engine empty for user senders (no engine field)', () => {
    const msg = baseMsg({
      id: 'mu-eng',
      participant_id: 'user-alice',
      content: 'hi',
    })
    render(
      <MessageBubble message={msg} participants={participants} isMine={false} />,
    )
    const avatar = screen.getByTestId('message-avatar')
    // Fixture omits engine on user-alice; the avatar mock stringifies
    // undefined to '' so callers can distinguish agent vs user.
    expect(avatar.getAttribute('data-engine')).toBe('')
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

// Issue #238 — accepted orchestrator handoff messages render as a
// dedicated breathing-border card instead of bleeding the raw
// ``[HANDOFF] <@user:...>`` protocol text into the chat.
describe('MessageBubble — handoff variant', () => {
  function handoffMsg(overrides: Partial<ChatMessage> = {}): ChatMessage {
    return baseMsg({
      id: 'mh',
      participant_id: 'agent-rep',
      content: '[HANDOFF] <@user:agent-1> Round 1 자기소개를 부탁드립니다.',
      metadata: {
        next_speaker_participant_id: 'agent-1',
        mentions: [{ type: 'user', id: 'agent-1' }],
      },
      ...overrides,
    })
  }

  it('delegates to HandoffMessageCard with the resolved target name', () => {
    render(
      <MessageBubble
        message={handoffMsg()}
        participants={participants}
        isMine={false}
      />,
    )
    const card = screen.getByTestId('handoff-card')
    expect(card).toHaveAttribute('data-state', 'pending')
    expect(screen.getByTestId('handoff-target-caption')).toHaveTextContent(
      'Helper',
    )
    // The raw protocol tokens are NOT surfaced in the card body.
    expect(card).not.toHaveTextContent('[HANDOFF]')
    expect(card).not.toHaveTextContent('<@user:agent-1>')
  })

  it('shows resolved state when handoffResolvedAt prop is non-null', () => {
    render(
      <MessageBubble
        message={handoffMsg()}
        participants={participants}
        isMine={false}
        handoffResolvedAt={new Date().toISOString()}
      />,
    )
    const card = screen.getByTestId('handoff-card')
    expect(card).toHaveAttribute('data-state', 'resolved')
  })

  it('does not enter the handoff branch when metadata is missing', () => {
    const m = baseMsg({
      id: 'mnh',
      content: '[HANDOFF] <@user:agent-1> hi',
      // no metadata → defensive parse returns null, fall back to plain.
    })
    render(
      <MessageBubble
        message={m}
        participants={participants}
        isMine={false}
      />,
    )
    expect(screen.queryByTestId('handoff-card')).toBeNull()
  })
})

// Issue #238 — workers sometimes append a ``handoff_to: ...`` trailer
// to their replies. Render-time strip keeps the wire body intact.
describe('MessageBubble — handoff_to trailer stripping', () => {
  it('removes a trailing handoff_to block from an agent reply', () => {
    const msg = baseMsg({
      content:
        '정리된 답변입니다.\n\nhandoff_to: <@user:agent-rep> participant_id: agent-rep',
    })
    render(
      <MessageBubble
        message={msg}
        participants={participants}
        isMine={false}
      />,
    )
    expect(screen.getByText('정리된 답변입니다.')).toBeInTheDocument()
    expect(screen.queryByText(/handoff_to:/)).toBeNull()
  })
})

// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import SharedRoomConversation from './SharedRoomConversation'
import { useSharedRoom, type SharedRoomState } from '@/hooks/useSharedRoom'
import type { SharedRoomSnapshot } from '@/lib/federationApi'

vi.mock('@/components/Sidebar', () => ({ default: () => null }))
vi.mock('@/components/SidebarExpandButton', () => ({ default: () => null }))
vi.mock('@/hooks/useRooms', () => ({ useRooms: () => ({ markRoomRead: vi.fn() }) }))
vi.mock('@/hooks/useSharedRoom', () => ({ useSharedRoom: vi.fn() }))
afterEach(cleanup)

const channel = { authority_node_id: 'authority', channel_id: 'channel' }
const currentRoom = { id: 'room', project_id: 'project', name: 'Shared room', is_dm: false, shared_channel: channel }
const message = { message_id: 'm1', ...channel, actor: { node_id: 'authority', kind: 'human' as const, principal_id: 'member' }, actor_name: 'Mina', seq: 1, thread_root_id: null, confirmed: true, text: 'Review this plan' }
function view(): SharedRoomState {
  const data: SharedRoomSnapshot = {
    ...channel, applied_seq: 1, messages: [message], participants: [],
    permissions: { can_send: true, can_delegate: true }, targets: [], delegations: [], submissions: [],
    cursor: { oldest_seq: 1, newest_seq: 1, has_more_before: false },
  }
  return { data, error: null, actionError: null, loading: false, busy: false, refresh: vi.fn(), loadOlder: vi.fn(), clearActionError: vi.fn(), delegate: vi.fn(), sendMessage: vi.fn(), cancel: vi.fn(), retry: vi.fn() }
}

describe('SharedRoomConversation', () => {
  it('keeps a confirmed delivery visible until the mirror applies its event', () => {
    const room = view()
    room.data!.submissions = [{ request_id: 'request-id', state: 'confirmed', kind: 'task.request', error_code: null, source_message_id: 'm1', delegation_id: 'd1', executor: null, can_retry: false, receipt: { state: 'requested', task_status: 'todo', process_state: 'not_started', seq: 2, revision: 1 } }]
    vi.mocked(useSharedRoom).mockReturnValue(room)
    const { rerender } = render(<SharedRoomConversation currentRoom={currentRoom} channel={channel} />)
    expect(screen.getByText('Delivery confirmed · updating shared conversation')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Delegate to an agent' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Retry delivery' })).toBeNull()
    room.data = { ...room.data!, applied_seq: 2 }
    rerender(<SharedRoomConversation currentRoom={currentRoom} channel={channel} />)
    expect(screen.queryByText('Delivery confirmed · updating shared conversation')).toBeNull()
  })

  it('only offers delegation on confirmed root messages', () => {
    const room = view()
    room.data!.messages.push({ ...message, message_id: 'reply', thread_root_id: 'm1', text: 'Reply', seq: 2 }, { ...message, message_id: 'pending', text: 'Unconfirmed root', seq: 3, confirmed: false })
    vi.mocked(useSharedRoom).mockReturnValue(room)
    render(<SharedRoomConversation currentRoom={currentRoom} channel={channel} />)
    expect(screen.getAllByRole('button', { name: 'Delegate to an agent' })).toHaveLength(1)
    expect(screen.getAllByRole('button', { name: 'Reply to message' })).toHaveLength(1)
    expect(screen.getByText('Reply')).toBeVisible()
  })

  it('removes writable controls after access is revoked and offers a refresh', () => {
    const room = { ...view(), data: null, error: 'SCOPE_DENIED' }
    vi.mocked(useSharedRoom).mockReturnValue(room)
    render(<SharedRoomConversation currentRoom={currentRoom} channel={channel} />)
    expect(screen.getByRole('alert')).toHaveTextContent('You no longer have access')
    expect(screen.getByRole('button', { name: 'Try again' })).toBeEnabled()
    expect(screen.queryByRole('textbox')).toBeNull()
    expect(screen.queryByText(/Start a shared conversation/)).toBeNull()
  })
})

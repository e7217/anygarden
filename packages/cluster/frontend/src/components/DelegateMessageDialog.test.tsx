// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import DelegateMessageDialog from './DelegateMessageDialog'
import DelegationCard from './DelegationCard'
import type { SharedRoomState } from '@/hooks/useSharedRoom'
import type { FederationMessage, SharedDelegation, SharedTarget } from '@/lib/federationApi'

afterEach(cleanup)
const message: FederationMessage = { message_id: 'source-id', authority_node_id: 'authority', channel_id: 'channel', actor: { node_id: 'authority', principal_id: 'human', kind: 'human' }, seq: 1, thread_root_id: null, confirmed: true, text: 'Review this proposal' }
const target: SharedTarget = { node_id: 'remote-node-uuid', agent_id: 'remote-agent-uuid', name: 'Reviewer', node_name: 'Studio', server_label: null, is_local: false, description: 'Reviews architecture', can_execute: true, unavailable_code: null }
function state(targets = [target]): SharedRoomState {
  return {
    data: { targets, submissions: [] }, busy: false, error: null, actionError: null,
    clearActionError: vi.fn(), delegate: vi.fn(async () => true), cancel: vi.fn(async () => true),
  } as unknown as SharedRoomState
}
const delegation: SharedDelegation = {
  delegation_id: 'delegation-uuid', task_id: 'task-uuid', source_message_id: 'source-id', requester: message.actor,
  executor: { node_id: target.node_id, agent_id: target.agent_id }, revision: 7,
  state: 'running', process_state: 'running', task_status: 'in_progress', result_markdown: null, error: null, can_cancel: true,
}

describe('shared delegation interaction', () => {
  it('uses named targets and sends only source/target/request identities', async () => {
    const room = state()
    render(<DelegateMessageDialog message={message} room={room} disabled={false} />)
    fireEvent.click(screen.getByRole('button', { name: 'Delegate to an agent' }))
    expect(screen.getByText('Studio')).toBeVisible()
    expect(screen.queryByText(target.agent_id)).toBeNull()
    expect(screen.getByRole('button', { name: 'Delegate message' })).toBeDisabled()
    fireEvent.click(screen.getByRole('radio', { name: /Reviewer/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Delegate message' }))
    await waitFor(() => expect(room.delegate).toHaveBeenCalledTimes(1))
    expect(room.delegate).toHaveBeenCalledWith({ request_id: expect.any(String), source_message_id: 'source-id', executor: { node_id: target.node_id, agent_id: target.agent_id } })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  it('disables unpublished names and unavailable machines even when visible', () => {
    const room = state([{ ...target, name: null }, { ...target, agent_id: 'offline', name: 'Offline', can_execute: false, unavailable_code: 'MACHINE_OFFLINE' }])
    render(<DelegateMessageDialog message={message} room={room} disabled={false} />)
    fireEvent.click(screen.getByRole('button', { name: 'Delegate to an agent' }))
    expect(screen.getByRole('radio', { name: /Agent name unavailable/ })).toBeDisabled()
    expect(screen.getByRole('radio', { name: /Offline/ })).toBeDisabled()
    expect(screen.getByText('Agent machine is offline')).toBeVisible()
  })

  it('keeps the same request id and selection after a failed delivery', async () => {
    const room = state()
    vi.mocked(room.delegate).mockResolvedValue(false)
    const { rerender } = render(<DelegateMessageDialog message={message} room={room} disabled={false} />)
    fireEvent.click(screen.getByRole('button', { name: 'Delegate to an agent' }))
    fireEvent.click(screen.getByRole('radio', { name: /Reviewer/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Delegate message' }))
    await waitFor(() => expect(room.delegate).toHaveBeenCalledTimes(1))
    rerender(<DelegateMessageDialog message={message} room={{ ...room, actionError: 'CONNECTION_FAILED' }} disabled={false} />)
    expect(screen.getByRole('alert')).toHaveTextContent('Your input is kept')
    fireEvent.click(screen.getByRole('button', { name: 'Delegate message' }))
    await waitFor(() => expect(room.delegate).toHaveBeenCalledTimes(2))
    expect(vi.mocked(room.delegate).mock.calls[0]).toEqual(vi.mocked(room.delegate).mock.calls[1])
  })

  it('requests cancellation with the hidden current revision and retains confirmed running state', async () => {
    const room = state()
    render(<DelegationCard delegation={delegation} target={target} room={room} />)
    expect(screen.getByText('Running')).toBeVisible()
    expect(screen.queryByText('delegation-uuid')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Request stop' }))
    expect(room.cancel).toHaveBeenCalledWith('delegation-uuid', { request_id: expect.any(String), expected_revision: 7 })
    expect(screen.getByText('Running')).toBeVisible()
  })

  it('shows confirmed result and never offers another execution for unknown outcomes', () => {
    const room = state()
    const { rerender } = render(<DelegationCard delegation={{ ...delegation, state: 'completed', can_cancel: false, result_markdown: '**Reviewed successfully**' }} target={target} room={room} />)
    expect(screen.getByText('Reviewed successfully')).toBeVisible()
    expect(screen.queryByRole('button')).toBeNull()
    rerender(<DelegationCard delegation={{ ...delegation, state: 'unknown', can_cancel: false }} target={target} room={room} />)
    expect(screen.getByText(/Another execution will not/)).toBeVisible()
    expect(screen.queryByRole('button')).toBeNull()
  })
})

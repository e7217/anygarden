// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import WorkspaceActionCard, { WorkspaceActionResultNote } from './WorkspaceActionCard'
import type { WorkspaceAction } from '@/lib/workspaceActions'

const api = vi.hoisted(() => ({ getWorkspaceAction: vi.fn(), decideWorkspaceAction: vi.fn() }))
vi.mock('@/lib/workspaceActions', async importOriginal => ({
  ...(await importOriginal<typeof import('@/lib/workspaceActions')>()),
  ...api,
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function request(overrides: Partial<WorkspaceAction> = {}): WorkspaceAction {
  return {
    id: 'req-1', room_id: 'room-1', agent_id: 'agent-1', action: 'create_routine',
    summary: 'Create a routine: Morning report', params: { title: 'Morning report', permission_level: 'trusted' }, status: 'pending', result: null, error: null,
    admin_only: false, can_decide: true, expires_at: '2026-10-27T05:00:00Z', ...overrides,
  }
}

const meta = { request_id: 'req-1', action: 'create_routine' }

describe('WorkspaceActionCard (#938)', () => {
  it('[WSP-04] lets the person approve and shows the outcome', async () => {
    api.getWorkspaceAction.mockResolvedValue(request())
    api.decideWorkspaceAction.mockResolvedValue(request({ status: 'executed', can_decide: false }))
    render(<WorkspaceActionCard meta={meta} fallbackText="Approval needed" />)

    expect(await screen.findByText('Create a routine: Morning report')).toBeInTheDocument()
    expect(screen.getByTestId('workspace-action-params')).toHaveTextContent('"permission_level": "trusted"')
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }))
    await waitFor(() => expect(screen.getByTestId('workspace-action-status')).toHaveTextContent('Approved and done'))
    expect(api.decideWorkspaceAction).toHaveBeenCalledWith('req-1', 'approve')
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull()
  })

  it('shows no buttons to someone who cannot decide and says when only an admin can', async () => {
    api.getWorkspaceAction.mockResolvedValue(request({ can_decide: false, admin_only: true }))
    render(<WorkspaceActionCard meta={meta} fallbackText="Approval needed" />)

    expect(await screen.findByText('Only an admin can approve this.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Reject' })).toBeNull()
  })

  it('refreshes when the outcome note for the same request appears', async () => {
    api.getWorkspaceAction.mockResolvedValueOnce(request()).mockResolvedValueOnce(request({ status: 'rejected', can_decide: false }))
    render(<WorkspaceActionCard meta={meta} fallbackText="Approval needed" />)
    await screen.findByRole('button', { name: 'Approve' })

    render(<WorkspaceActionResultNote meta={{ request_id: 'req-1', status: 'rejected' }} />)
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull())
    expect(api.getWorkspaceAction).toHaveBeenCalledTimes(2)
  })
})

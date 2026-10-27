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
    id: 'req-1', room_id: 'room-1', agent_id: 'agent-1', action: 'hire_agent',
    summary: 'Propose a new agent: Scout', params: { name: 'Scout', permission_level: 'trusted' }, status: 'pending', result: null, error: null,
    admin_only: false, can_decide: true, expires_at: '2026-10-27T05:00:00Z', ...overrides,
  }
}

const meta = { request_id: 'req-1', action: 'hire_agent' }

describe('WorkspaceActionCard (#938)', () => {
  it('[WSP-04] lets the person approve and shows the outcome', async () => {
    api.getWorkspaceAction.mockResolvedValue(request())
    api.decideWorkspaceAction.mockResolvedValue(request({ status: 'executed', can_decide: false }))
    render(<WorkspaceActionCard meta={meta} fallbackText="Approval needed" />)

    expect(await screen.findByText('Propose a new agent: Scout')).toBeInTheDocument()
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

describe('WorkspaceActionCard drafts (#947, #948)', () => {
  const routine = {
    agent_id: 'agent-1', title: 'Morning report', spec: 'Summarise failed tasks', trigger_type: 'cron',
    trigger_config: { cron: '0 9 * * *', timezone: 'Asia/Seoul' }, report_room_id: 'room-1', materialize: 'interesting_only',
  }
  const participants = { p1: { id: 'p1', display_name: 'Builder', kind: 'agent' as const, agent_id: 'agent-1' } }

  it('[WSP-07] shows a routine draft in words, with its schedule, timezone and assignee', async () => {
    api.getWorkspaceAction.mockResolvedValue(request({ action: 'create_routine', params: routine }))
    render(<WorkspaceActionCard meta={{ request_id: 'req-1', action: 'create_routine' }} fallbackText="x" participants={participants} />)

    const summary = await screen.findByTestId('draft-summary-routine')
    expect(summary).toHaveTextContent('Every day at 09:00')
    expect(summary).toHaveTextContent('0 9 * * *')
    expect(summary).toHaveTextContent('Asia/Seoul')
    expect(summary).toHaveTextContent('Builder')
    expect(summary).toHaveTextContent('This room')
    expect(screen.getByRole('button', { name: 'Create' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Edit in form' })).toBeInTheDocument()
  })

  it('[WSP-08] approves with the values edited in the form', async () => {
    api.getWorkspaceAction.mockResolvedValue(request({ action: 'create_routine', params: routine }))
    api.decideWorkspaceAction.mockResolvedValue(request({ action: 'create_routine', params: routine, status: 'executed', can_decide: false }))
    render(<WorkspaceActionCard meta={{ request_id: 'req-1', action: 'create_routine' }} fallbackText="x" participants={participants} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Edit in form' }))
    const title = await screen.findByDisplayValue('Morning report')
    fireEvent.change(title, { target: { value: 'Weekday report' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add routine' }))
    await waitFor(() => expect(api.decideWorkspaceAction).toHaveBeenCalledTimes(1))
    const [, decision, params] = api.decideWorkspaceAction.mock.calls[0]
    expect(decision).toBe('approve')
    expect(params).toMatchObject({
      agent_id: 'agent-1', title: 'Weekday report', trigger_type: 'cron',
      trigger_config: { cron: '0 9 * * *', timezone: 'Asia/Seoul' },
    })
  })

  it('[WSP-06] shows a goal draft with its evaluator command', async () => {
    const spec = {
      title: 'Faster search', purpose: 'p95 under a second', mode: 'target',
      metric: { name: 'p95_ms', direction: 'minimize', target: 1000 },
      evaluator: { command: 'python3 bench.py', score_key: 'p95_ms' }, budget: { max_total_tokens: 200000 },
    }
    api.getWorkspaceAction.mockResolvedValue(request({ action: 'create_goal', params: { room_id: 'room-1', spec } }))
    render(<WorkspaceActionCard meta={{ request_id: 'req-1', action: 'create_goal' }} fallbackText="x" />)

    const summary = await screen.findByTestId('draft-summary-goal')
    expect(summary).toHaveTextContent('python3 bench.py')
    expect(summary).toHaveTextContent('p95_ms')
    expect(summary).toHaveTextContent('1000')
  })
})

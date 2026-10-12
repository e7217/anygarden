// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, screen, fireEvent, cleanup, waitFor, within } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'

const apiFetch = vi.fn()
vi.mock('@/lib/api', () => ({ apiFetch: (...args: unknown[]) => apiFetch(...args) }))

const authState = { isAdmin: true }
vi.mock('@/hooks/useAuth', () => ({
  useAuth: () => ({ user: { id: 'u1', email: 'a@example.com', is_admin: authState.isAdmin }, loading: false }),
}))

import AdminBudgets from './AdminBudgets'

function json(body: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => body }
}

const policies = [
  {
    id: 'p-global', scope_type: 'global', scope_id: null, token_ceiling: 1000000,
    warn_percent: 80, window_kind: 'rolling_24h', hard_stop_enabled: false, is_active: true,
    created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z',
  },
  {
    id: 'p-agent', scope_type: 'agent', scope_id: 'a1', token_ceiling: 1000,
    warn_percent: 80, window_kind: 'rolling_24h', hard_stop_enabled: true, is_active: true,
    created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z',
  },
]

const overview = {
  usage: [
    { policy_id: 'p-global', observed_tokens: 2500, window_start: '2026-10-11T00:00:00Z' },
    { policy_id: 'p-agent', observed_tokens: 1200, window_start: '2026-10-11T00:00:00Z' },
  ],
  incidents: [{
    id: 'i1', policy_id: 'p-agent', scope_type: 'agent', scope_id: 'a1', threshold_type: 'hard',
    status: 'open', observed_tokens: 1200, window_start: '2026-10-11T00:00:00Z', created_at: '2026-10-12T01:00:00Z',
  }],
  paused_agents: [{ agent_id: 'a1', name: 'Writer' }],
}

beforeEach(() => {
  authState.isAdmin = true
  apiFetch.mockReset()
  apiFetch.mockImplementation(async (url: string, init?: RequestInit) => {
    if (url === '/api/v1/budgets' && init?.method === 'POST') {
      return json({ ...policies[1], id: 'p-new' }, 201)
    }
    if (url === '/api/v1/budgets') return json(policies)
    if (url === '/api/v1/budgets/overview') return json(overview)
    if (url === '/api/v1/agents') return json([{ id: 'a1', name: 'Writer' }, { id: 'a2', name: 'Reviewer' }])
    if (url.startsWith('/api/v1/rooms')) return json([{ id: 'r1', name: 'General' }])
    return json({}, 404)
  })
})
afterEach(cleanup)

describe('AdminBudgets', () => {
  it('[USE-07] renders policies with usage vs ceiling, hard stop and status text', async () => {
    render(<AdminBudgets />)
    const agentRow = await screen.findByTestId('budget-policy-p-agent')
    expect(within(agentRow).getByText('Agent · Writer')).toBeInTheDocument()
    expect(within(agentRow).getByText('1,200 of 1,000 tokens')).toBeInTheDocument()
    expect(within(agentRow).getByText('Over ceiling')).toBeInTheDocument()
    expect(within(agentRow).getByText('Hard stop on')).toBeInTheDocument()
    expect(within(agentRow).getByRole('progressbar')).toHaveAttribute('aria-valuetext', '120% used')

    const globalRow = screen.getByTestId('budget-policy-p-global')
    expect(within(globalRow).getByText('Whole workspace')).toBeInTheDocument()
    expect(within(globalRow).getByText('Within budget')).toBeInTheDocument()
    expect(within(globalRow).getByText('Record only')).toBeInTheDocument()

    // Open incident and paused agent surface with a resume action.
    expect(screen.getByTestId('budget-incident-i1')).toHaveTextContent('Ceiling reached')
    expect(screen.getByRole('button', { name: 'Resume Writer' })).toBeInTheDocument()
  })

  it('[USE-07] create flow posts the scope, ceiling and hard-stop payload', async () => {
    render(<AdminBudgets />)
    await screen.findByTestId('budget-policy-p-agent')
    fireEvent.click(screen.getAllByRole('button', { name: 'New policy' })[0])

    const dialog = await screen.findByRole('dialog')
    // Advanced fields stay hidden on the typical path.
    expect(within(dialog).queryByLabelText('Warning threshold (%)')).not.toBeInTheDocument()

    // Validation: missing target and ceiling are reported, nothing is posted.
    fireEvent.change(within(dialog).getByLabelText('Applies to'), { target: { value: 'agent' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create policy' }))
    expect(await within(dialog).findByText('Choose what this policy applies to.')).toBeInTheDocument()
    expect(within(dialog).getByText('Enter a whole number of at least 1.')).toBeInTheDocument()
    expect(apiFetch.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)

    fireEvent.change(within(dialog).getByLabelText('Agent'), { target: { value: 'a2' } })
    fireEvent.change(within(dialog).getByLabelText('Token ceiling'), { target: { value: '50000' } })
    fireEvent.click(within(dialog).getByLabelText('Hard stop'))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create policy' }))

    await waitFor(() => {
      expect(apiFetch.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(true)
    })
    const [url, init] = apiFetch.mock.calls.find(([, i]) => i?.method === 'POST')!
    expect(url).toBe('/api/v1/budgets')
    expect(JSON.parse(init.body)).toEqual({
      scope_type: 'agent',
      scope_id: 'a2',
      token_ceiling: 50000,
      warn_percent: 80,
      window_kind: 'rolling_24h',
      hard_stop_enabled: true,
    })
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('[USE-07] edit sends only changed fields', async () => {
    render(<AdminBudgets />)
    await screen.findByTestId('budget-policy-p-global')
    fireEvent.click(screen.getByRole('button', { name: 'Edit policy for Whole workspace' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.change(within(dialog).getByLabelText('Token ceiling'), { target: { value: '2000000' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => {
      expect(apiFetch.mock.calls.some(([, init]) => init?.method === 'PATCH')).toBe(true)
    })
    const [url, init] = apiFetch.mock.calls.find(([, i]) => i?.method === 'PATCH')!
    expect(url).toBe('/api/v1/budgets/p-global')
    expect(JSON.parse(init.body)).toEqual({ token_ceiling: 2000000 })
  })

  it('[USE-07] is hidden from non-admins and makes no budget requests', () => {
    authState.isAdmin = false
    const { container } = render(<AdminBudgets />)
    expect(container).toBeEmptyDOMElement()
    expect(apiFetch).not.toHaveBeenCalled()
  })
})

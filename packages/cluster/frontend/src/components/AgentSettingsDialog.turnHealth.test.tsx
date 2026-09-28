// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup, waitFor, within } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter } from 'react-router-dom'

vi.mock('@/components/EngineGlyph', () => ({
  EngineGlyph: ({ engine }: { engine: string | undefined }) => <svg data-testid={`engine-${engine ?? 'none'}`} />,
}))
vi.mock('@/lib/api', () => ({ apiFetch: vi.fn() }))

import AgentSettingsDialog from './AgentSettingsDialog'
import { apiFetch } from '@/lib/api'
import type { Agent } from '@/hooks/useAgents'

const fetchMock = vi.mocked(apiFetch)
afterEach(() => { cleanup(); vi.resetAllMocks() })

type Row = { id: string; event_type: string; timestamp: string; request_id: string | null; details: Record<string, unknown> | null }

function serve(activity: Row[]) {
  fetchMock.mockImplementation(async (url: string) => ({
    ok: true,
    json: async () => (String(url).includes('/activity') ? activity : []),
  }) as Response)
}

function failedTurn(error: string): Row[] {
  return [
    { id: 'e3', event_type: 'handler_finished', timestamp: '2026-09-28T02:00:03.000000Z', request_id: 'turn-new', details: { outcome: 'failed', error } },
    { id: 'e2', event_type: 'handler_started', timestamp: '2026-09-28T02:00:00.000000Z', request_id: 'turn-new', details: { room_id: 'room-1' } },
    { id: 'e1', event_type: 'handler_finished', timestamp: '2026-09-28T01:00:02.000000Z', request_id: 'turn-old', details: { outcome: 'ok' } },
    { id: 'e0', event_type: 'handler_started', timestamp: '2026-09-28T01:00:00.000000Z', request_id: 'turn-old', details: null },
  ]
}

function renderDialog(agent: Partial<Agent> = {}) {
  render(
    <MemoryRouter>
      <AgentSettingsDialog
        agent={{ id: 'a1', name: 'pi-bot', engine: 'claude-code', desired_state: 'running', actual_state: 'running', restart_policy: 'always', agents_md: null, ...agent }}
        open
        onOpenChange={vi.fn()}
        fetchAgentFiles={vi.fn().mockResolvedValue([])}
        updateAgent={vi.fn()}
        upsertAgentFile={vi.fn()}
        deleteAgentFile={vi.fn()}
        fetchEngineCatalog={vi.fn().mockResolvedValue(null)}
      />
    </MemoryRouter>,
  )
}

describe('AgentSettingsDialog recent turn health (#716)', () => {
  it('shows a failed latest turn beside an Online process state', async () => {
    serve(failedTurn('missing_terminal_event'))
    renderDialog()
    const summary = await screen.findByTestId('overview-recent-turn')
    await waitFor(() => expect(summary).toHaveAttribute('data-status', 'failed'))
    // The process indicator is untouched: still running.
    expect(within(screen.getByTestId('agent-settings-section-overview')).getByText('running')).toBeInTheDocument()
    expect(summary).toHaveTextContent('Failed — no reply sent')
    expect(screen.getByTestId('overview-recent-turn-reason')).toHaveTextContent('Engine failure')
    expect(screen.getByTestId('overview-recent-turn-reason')).toHaveTextContent('exited without reporting completion')
    expect(screen.getByTestId('overview-recent-turn-reason')).toHaveTextContent('missing_terminal_event')
  })

  it.each([
    ['POLICY_DENIED', 'Blocked by execution policy'],
    ['ENGINE_AUTH_ERROR', 'Model connection'],
    ['TIMEOUT_STOPPED', 'Timed out'],
  ])('labels %s as %s', async (error, label) => {
    serve(failedTurn(error))
    renderDialog()
    expect(await screen.findByTestId('overview-recent-turn-reason')).toHaveTextContent(label)
  })

  it('never prints free-form engine text in the summary', async () => {
    serve(failedTurn('Error: 401 from provider {"api_key":"sk-live-secret"}'))
    renderDialog()
    const reason = await screen.findByTestId('overview-recent-turn-reason')
    expect(reason).toHaveTextContent('Engine failure')
    expect(screen.getByTestId('overview-recent-turn')).not.toHaveTextContent('sk-live-secret')
  })

  it('reports a successful latest turn without a failure reason', async () => {
    serve(failedTurn('ENGINE_ERROR').slice(2))
    renderDialog()
    const summary = await screen.findByTestId('overview-recent-turn')
    await waitFor(() => expect(summary).toHaveAttribute('data-status', 'succeeded'))
    expect(summary).toHaveTextContent('Replied')
    expect(screen.queryByTestId('overview-recent-turn-reason')).toBeNull()
  })

  it('opens Activity with the failed turn expanded', async () => {
    serve(failedTurn('POLICY_DENIED'))
    renderDialog()
    fireEvent.click(await screen.findByTestId('overview-recent-turn-details'))
    expect(screen.getByRole('tab', { name: 'Activity' })).toHaveAttribute('aria-selected', 'true')
    const activity = screen.getByTestId('agent-settings-section-activity')
    const row = await waitFor(() => {
      const found = activity.querySelector('[data-request-id="turn-new"]')
      expect(found).not.toBeNull()
      return found as HTMLElement
    })
    await waitFor(() => expect(row).toHaveTextContent('handler_finished'))
  })
})

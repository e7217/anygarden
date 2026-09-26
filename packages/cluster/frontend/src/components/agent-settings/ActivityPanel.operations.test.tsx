// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import ActivityPanel from './ActivityPanel'
import { apiFetch } from '@/lib/api'

vi.mock('@/lib/api', () => ({ apiFetch: vi.fn() }))
const fetchMock = vi.mocked(apiFetch)
afterEach(() => { cleanup(); vi.resetAllMocks() })

it('distinguishes loading and failure from empty history, with retry and refresh', async () => {
  let finish!: (response: Response) => void
  fetchMock.mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
  render(<ActivityPanel agentId="agent-a" />)
  expect(screen.getByRole('status')).toHaveTextContent('Loading activity')
  expect(screen.queryByText('No activity yet')).not.toBeInTheDocument()
  await act(async () => { finish({ ok: false } as Response) })
  expect(screen.getByRole('alert')).toHaveTextContent('Activity could not be loaded')
  expect(screen.queryByText('No activity yet')).not.toBeInTheDocument()
  fetchMock.mockResolvedValue({ ok: true, json: async () => [] } as Response)
  fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
  expect(await screen.findByText('No activity yet')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3))
})

it('keeps rows visible after an older page fails and exposes retry', async () => {
  const rows = Array.from({ length: 51 }, (_, id) => ({ id: `event-${id}`, timestamp: '2026-09-26T12:00:00.000000+00:00', event_type: 'start_requested', request_id: null, details: null }))
  fetchMock.mockResolvedValueOnce({ ok: true, json: async () => rows } as Response).mockResolvedValueOnce({ ok: false } as Response)
  render(<ActivityPanel agentId="agent-a" />)
  fireEvent.click(await screen.findByRole('button', { name: 'Load older activity' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('previously loaded records are still shown')
  expect(screen.getAllByText('start_requested')).toHaveLength(50)
  expect(screen.getByRole('button', { name: 'Try again' })).toBeEnabled()
})

it('does not request activity while its settings section is collapsed', () => {
  render(<ActivityPanel agentId="agent-a" active={false} />)
  expect(fetchMock).not.toHaveBeenCalled()
})

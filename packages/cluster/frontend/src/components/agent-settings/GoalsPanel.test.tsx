// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter, useLocation } from 'react-router-dom'
import GoalsPanel from './GoalsPanel'
import { apiFetch } from '@/lib/api'
vi.mock('@/lib/api', () => ({ apiFetch: vi.fn() }))
const fetch = vi.mocked(apiFetch)
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
const goal = { id: 'goal-A', assignee_agent_id: 'A', title: 'Inspect daily', spec: 'Check server.', trigger_type: 'manual', trigger_config: {}, materialize: 'full', report_room_id: 'room-A', status: 'active', consecutive_failures: 0 }
const Location = () => <output>{useLocation().pathname}</output>
afterEach(() => { cleanup(); vi.resetAllMocks() })

it('shows query failure with retry and closes settings on report room navigation', async () => {
  fetch.mockResolvedValue(json({}, 503))
  const close = vi.fn()
  render(<MemoryRouter><GoalsPanel agentId="A" onNavigateAway={close} /><Location /></MemoryRouter>)
  expect(await screen.findByRole('alert')).toHaveTextContent(/could not load/i)
  expect(screen.queryByText('This agent has no responsibilities yet')).not.toBeInTheDocument()
  fetch.mockResolvedValue(json([goal]))
  fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Open report room' }))
  expect(close).toHaveBeenCalledOnce()
  expect(screen.getByText('/rooms/room-A')).toBeInTheDocument()
})

it('loads existing responsibility fields into the edit form', async () => {
  fetch.mockResolvedValue(json([goal]))
  render(<MemoryRouter><GoalsPanel agentId="A" agentName="Builder" /></MemoryRouter>)
  fireEvent.click(await screen.findByRole('button', { name: 'Edit responsibility' }))
  expect(screen.getByLabelText('Title')).toHaveValue(goal.title)
  expect(screen.getByLabelText('Instructions')).toHaveValue(goal.spec)
  expect(screen.getByLabelText('Report to room (room ID)')).toHaveValue('room-A')
})

it('isolates goals and unfinished edits when the selected agent changes', async () => {
  let resolve!: (response: Response) => void
  fetch.mockReturnValueOnce(new Promise<Response>(done => { resolve = done })).mockResolvedValueOnce(json([{ ...goal, title: 'Agent B goal' }]))
  const { rerender } = render(<MemoryRouter><GoalsPanel agentId="A" /></MemoryRouter>)
  rerender(<MemoryRouter><GoalsPanel agentId="B" /></MemoryRouter>)
  expect(await screen.findByText('Agent B goal')).toBeInTheDocument()
  await act(async () => resolve(json([goal])))
  expect(screen.queryByText(goal.title)).not.toBeInTheDocument()
})

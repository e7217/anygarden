// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter, useLocation } from 'react-router-dom'
import TasksPanel from './TasksPanel'
import { apiFetch } from '@/lib/api'
vi.mock('@/lib/api', () => ({ apiFetch: vi.fn() }))
const fetch = vi.mocked(apiFetch)
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
const task = { id: 'task-A', title: 'Current task', status: 'todo', room_id: 'room-A', room_name: 'Alpha room' }
const Location = () => <output>{useLocation().pathname}</output>
afterEach(() => { cleanup(); vi.resetAllMocks() })
describe('TasksPanel user actions', () => {
  it('shows an actionable query failure and recovers on retry', async () => {
    fetch.mockRejectedValue(new Error('Offline'))
    render(<MemoryRouter><TasksPanel agentId="A" /></MemoryRouter>)
    expect(await screen.findByRole('alert')).toHaveTextContent(/could not load/i)
    expect(screen.queryByText('No tasks assigned')).not.toBeInTheDocument()
    fetch.mockResolvedValue(json([task]))
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('Current task')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
  it('closes the hosting dialog before navigating to the task room', async () => {
    fetch.mockResolvedValue(json([task]))
    const onNavigateAway = vi.fn()
    render(<MemoryRouter><TasksPanel agentId="A" onNavigateAway={onNavigateAway} /><Location /></MemoryRouter>)
    fireEvent.click(await screen.findByRole('button', { name: 'Alpha room' }))
    expect(onNavigateAway).toHaveBeenCalledOnce()
    expect(screen.getByText('/rooms/room-A')).toBeInTheDocument()
  })
  it('hides the previous agent immediately and discards late responses', async () => {
    let resolve!: (response: Response) => void
    fetch.mockReturnValueOnce(new Promise<Response>(done => { resolve = done })).mockResolvedValueOnce(json([{ ...task, title: 'Agent B task' }]))
    const { rerender } = render(<MemoryRouter><TasksPanel agentId="A" /></MemoryRouter>)
    rerender(<MemoryRouter><TasksPanel agentId="B" /></MemoryRouter>)
    expect(await screen.findByText('Agent B task')).toBeInTheDocument()
    await act(async () => resolve(json([task])))
    expect(screen.queryByText('Current task')).not.toBeInTheDocument()
    expect(screen.getByText('Agent B task')).toBeInTheDocument()
  })
})

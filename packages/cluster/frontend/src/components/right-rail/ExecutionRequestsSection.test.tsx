// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter } from 'react-router-dom'
import { RequestCard } from './ExecutionRequestsSection'
import type { ExecutionRequest } from '@/hooks/useExecutionRequests'

const request: ExecutionRequest = {
  id: 'question', execution_id: 'run', operating_room_id: 'room', task_id: 'task', task_room_id: 'room',
  task_title: 'Review display name', question: 'Which name should appear?', status: 'pending',
  can_answer: true, is_current: true, input_revision: 1, created_at: '2026-10-01T00:00:00Z',
}
afterEach(cleanup)
describe('compact pending question', () => {
  it('retains a draft when details are closed and reopened, and submits the same answer', async () => {
    const answer = vi.fn().mockResolvedValue({ ...request, status: 'answered' })
    render(<MemoryRouter><RequestCard compact request={request} answer={answer} /></MemoryRouter>)
    const article = screen.getByTestId('execution-request-question')
    const details = article.querySelector('details')!
    expect(details.open).toBe(false)
    details.open = true
    const input = screen.getByTestId('execution-request-answer-question')
    fireEvent.change(input, { target: { value: 'Anygarden' } })
    details.open = false
    details.open = true
    expect(input).toHaveValue('Anygarden')
    fireEvent.submit(input.closest('form')!)
    await waitFor(() => expect(answer).toHaveBeenCalledWith('question', 'Anygarden'))
    await waitFor(() => expect(input).toHaveValue(''))
  })
  it.each([{ can_answer: false }, { is_current: false }])('does not expose answer actions when disallowed: %j', changes => {
    render(<MemoryRouter><RequestCard compact request={{ ...request, ...changes }} answer={vi.fn()} /></MemoryRouter>)
    expect(screen.queryByTestId('execution-request-answer-question')).not.toBeInTheDocument()
  })
  it('keeps the expanded behavior for callers outside the rail', () => {
    render(<MemoryRouter><RequestCard request={request} answer={vi.fn()} /></MemoryRouter>)
    expect(screen.getByTestId('execution-request-question').querySelector('details')).toBeNull()
  })
})

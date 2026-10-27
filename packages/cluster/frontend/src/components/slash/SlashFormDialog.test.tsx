// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import SlashFormDialog from './SlashFormDialog'
import type { Participant } from '@/lib/participants'

vi.mock('@/components/feedback/FeedbackProvider', () => ({
  useFeedback: () => ({ notify: vi.fn(), confirm: vi.fn() }),
}))

afterEach(cleanup)

const participants: Record<string, Participant> = {
  p1: { id: 'p1', display_name: 'Builder', kind: 'agent', agent_id: 'agent-1' },
  p2: { id: 'p2', display_name: 'Analyst', kind: 'agent', agent_id: 'agent-2' },
  p3: { id: 'p3', display_name: 'Me', kind: 'user', user_id: 'u1' },
}

describe('SlashFormDialog (#940)', () => {
  it('stays closed without a request', () => {
    render(<SlashFormDialog roomId="room-1" participants={participants} request={null} onClose={vi.fn()} />)
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('[MSG-10] opens the routine form with the mentioned agent and the typed title', () => {
    render(
      <SlashFormDialog
        roomId="room-1"
        participants={participants}
        request={{ command: 'routine', payload: { assignee_pid: 'p2', title: 'Daily report' } }}
        onClose={vi.fn()}
      />,
    )
    expect(screen.getByRole('dialog', { name: 'New routine' })).toBeInTheDocument()
    expect(screen.getByDisplayValue('Daily report')).toBeInTheDocument()
    expect(screen.getByRole('combobox')).toHaveValue('agent-2')
  })

  it('[MSG-10] opens the goal form with the typed title', () => {
    render(
      <SlashFormDialog
        roomId="room-1"
        participants={participants}
        request={{ command: 'goal', payload: { title: 'Raise parser accuracy' } }}
        onClose={vi.fn()}
      />,
    )
    expect(screen.getByRole('dialog', { name: 'Set a goal' })).toBeInTheDocument()
    expect(screen.getByLabelText('Title')).toHaveValue('Raise parser accuracy')
  })
})

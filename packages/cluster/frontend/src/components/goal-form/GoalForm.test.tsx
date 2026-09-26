// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import GoalForm from './GoalForm'

const mocks = vi.hoisted(() => ({ createGoal: vi.fn() }))
vi.mock('@/lib/goals', () => ({ createGoal: mocks.createGoal }))

afterEach(() => {
  cleanup()
  mocks.createGoal.mockReset()
})

describe('GoalForm', () => {
  it('keeps the current room and default recording policy when advanced settings stay closed', async () => {
    mocks.createGoal.mockResolvedValue({ id: 'goal-1' })
    const onCreated = vi.fn()
    render(
      <GoalForm
        roomAgents={[{ id: 'agent-1', name: 'Builder' }]}
        defaultReportRoomId="room-1"
        onCreated={onCreated}
        onCancel={() => {}}
      />,
    )

    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Daily check' } })
    fireEvent.change(screen.getByLabelText('Instructions'), { target: { value: 'Check the host.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add responsibility' }))

    await waitFor(() => expect(mocks.createGoal).toHaveBeenCalledWith('agent-1', {
      title: 'Daily check',
      spec: 'Check the host.',
      trigger_type: 'cron',
      trigger_config: { cron: '0 9 * * *' },
      materialize: 'interesting_only',
      report_room_id: 'room-1',
    }))
    expect(onCreated).toHaveBeenCalledWith({ id: 'goal-1' })
  })
})

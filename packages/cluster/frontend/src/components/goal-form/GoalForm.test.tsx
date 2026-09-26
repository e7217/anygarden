// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import GoalForm from './GoalForm'
import type { Goal } from '@/lib/goals'

const mocks = vi.hoisted(() => ({ createGoal: vi.fn(), updateGoal: vi.fn() }))
vi.mock('@/lib/goals', () => ({ createGoal: mocks.createGoal, updateGoal: mocks.updateGoal }))

afterEach(() => {
  cleanup()
  mocks.createGoal.mockReset()
  mocks.updateGoal.mockReset()
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

const existing: Goal = {
  id: 'existing', assignee_agent_id: 'agent-1', owner_id: 'owner-1', title: 'Daily check', spec: 'Inspect the server.',
  trigger_type: 'cron', trigger_config: { cron: '0 8 * * *', timezone: 'Asia/Seoul' }, materialize: 'full', report_room_id: 'room-1',
  status: 'active', consecutive_failures: 0, next_run_at: null, last_run_at: null, created_at: '2026-09-26T00:00:00Z', updated_at: '2026-09-26T00:00:00Z',
}

it('updates existing fields while preserving hidden values and fixed ownership', async () => {
  mocks.updateGoal.mockResolvedValue(existing)
  render(<GoalForm goal={existing} roomAgents={[{ id: 'agent-1', name: 'Builder' }, { id: 'agent-2', name: 'Reviewer' }]} onCreated={() => {}} onCancel={() => {}} />)
  expect(screen.getByTestId('goal-form-assignee-select')).toBeDisabled()
  fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Updated check' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))
  await waitFor(() => expect(mocks.updateGoal).toHaveBeenCalledWith('existing', {
    title: 'Updated check',
  }))
  expect(mocks.createGoal).not.toHaveBeenCalled()
})

it('keeps failed edits for retry and explicitly clears an emptied report room', async () => {
  mocks.updateGoal.mockRejectedValueOnce(new Error('Save unavailable')).mockResolvedValueOnce(existing)
  const saved = vi.fn()
  render(<GoalForm goal={existing} roomAgents={[{ id: 'agent-1', name: 'Builder' }]} onCreated={saved} onCancel={() => {}} />)
  fireEvent.change(screen.getByLabelText('Report to room (room ID)'), { target: { value: '' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Save unavailable')
  expect(screen.getByLabelText('Title')).toHaveValue('Daily check')
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))
  await waitFor(() => expect(saved).toHaveBeenCalledOnce())
  expect(mocks.updateGoal).toHaveBeenLastCalledWith('existing', expect.objectContaining({ report_room_id: null }))
})

it('preserves additional trigger keys when editing the current schedule', async () => {
  mocks.updateGoal.mockResolvedValue(existing)
  render(<GoalForm goal={existing} roomAgents={[{ id: 'agent-1', name: 'Builder' }]} onCreated={() => {}} onCancel={() => {}} />)
  fireEvent.change(screen.getByRole('textbox', { name: 'Cron' }), { target: { value: '0 12 * * *' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))
  await waitFor(() => expect(mocks.updateGoal).toHaveBeenCalledWith('existing', { trigger_config: { cron: '0 12 * * *', timezone: 'Asia/Seoul' } }))
})

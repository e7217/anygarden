import { describe, expect, it } from 'vitest'
import { taskPresentation } from './taskPresentation'
import type { Task } from '@/hooks/useRoomTasks'

const task = (values: Partial<Task> = {}): Task => ({
  id: 'task', title: 'Review', room_id: 'room', status: 'in_progress',
  assignee_participant_id: null, created_at: '2026-10-01T00:00:00Z', ...values,
})

describe('task presentation across execution lifecycle', () => {
  it('moves confirmed cancellation out of current progress without mutating task state', () => {
    const original = task({ disposition: 'cancelled' })
    expect(taskPresentation(original)).toEqual({ status: 'cancelled', label: 'executions.status.cancelled', history: true })
    expect(original.status).toBe('in_progress')
  })
  it('keeps cancellation pending until stopping is confirmed', () => {
    expect(taskPresentation(task({ disposition: 'cancelling' }))).toEqual({ status: 'cancelling', label: 'executions.status.cancelling', history: false })
  })
  it.each(['deadline', 'limit'] as const)('distinguishes %s stopping from user cancellation', execution_operation_action => {
    const pending = taskPresentation(task({ disposition: 'cancelling', execution_operation_action }))
    const stopped = taskPresentation(task({ disposition: 'cancelled', execution_operation_action }))
    expect(pending.label).toBe(`executions.${execution_operation_action}.waitingStop`)
    expect(pending.history).toBe(false)
    expect(stopped.label).toBe(execution_operation_action === 'deadline' ? 'executions.deadline.expired' : 'executions.limit.stopped')
    expect(stopped.history).toBe(true)
  })
  it.each([{ disposition: 'superseded' as const }, { is_current: false }])('keeps older input out of active tasks: %j', values => {
    expect(taskPresentation(task(values))).toEqual({ status: 'superseded', label: 'executions.status.superseded', history: true })
  })
  it('preserves finished task results when an execution times out', () => {
    expect(taskPresentation(task({ status: 'done', execution_status: 'failed', execution_operation_action: 'deadline', result_version: 1 }))).toEqual({ status: 'done', label: 'tasks.done', history: true })
  })
  it('supports ordinary tasks from servers without execution fields', () => {
    expect(taskPresentation(task())).toEqual({ status: 'in_progress', label: 'tasks.inProgress', history: false })
  })
})

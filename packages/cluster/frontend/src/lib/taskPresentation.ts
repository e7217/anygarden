import type { Task } from '@/hooks/useRoomTasks'
import type { MessageKey } from '@/i18n/messages'

/** Keep task progress separate from the lifecycle of its execution input. */
export function taskPresentation(task: Pick<Task, 'status'> & Partial<Pick<Task, 'disposition' | 'is_current' | 'execution_operation_action'>>): { status: string; label?: MessageKey; history: boolean } {
  if (task.disposition === 'superseded' || task.is_current === false && !['cancelled', 'cancelling'].includes(task.disposition ?? '')) {
    return { status: 'superseded', label: 'executions.status.superseded', history: true }
  }
  if (task.disposition === 'cancelled' || task.disposition === 'cancelling') {
    const stopped = task.disposition === 'cancelled'
    const action = task.execution_operation_action
    const label = action === 'deadline'
      ? stopped ? 'executions.deadline.expired' : 'executions.deadline.waitingStop'
      : action === 'limit'
        ? stopped ? 'executions.limit.stopped' : 'executions.limit.waitingStop'
        : stopped ? 'executions.status.cancelled' : 'executions.status.cancelling'
    return { status: stopped ? 'cancelled' : 'cancelling', label, history: stopped }
  }
  const labels: Record<string, MessageKey> = {
    todo: 'tasks.todo', in_progress: 'tasks.inProgress', blocked: 'tasks.blocked', failed: 'tasks.failed', done: 'tasks.done',
  }
  return { status: task.status, label: labels[task.status], history: task.status === 'done' }
}

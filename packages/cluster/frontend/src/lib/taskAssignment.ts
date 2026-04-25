/**
 * Task assignment metadata helpers (#266).
 *
 * Server-injected synthetic messages carry a ``task_assignment`` block
 * inside their ``metadata``. The frontend uses it to render a compact
 * task card instead of a regular chat bubble — the channel still
 * carries the agent-mention so ``decide_policy`` wakes the assignee,
 * but the human-facing surface is a structured card.
 */
import type { ChatMessage } from '@/hooks/useWebSocket'

export type TaskAssignmentEvent = 'assigned' | 'reassigned'

export interface TaskAssignmentMeta {
  task_id: string
  assignee_pid: string
  event: TaskAssignmentEvent
}

export function parseTaskAssignment(
  message: ChatMessage,
): TaskAssignmentMeta | null {
  const meta = message.metadata
  if (!meta || typeof meta !== 'object') return null
  const raw = (meta as Record<string, unknown>).task_assignment
  if (!raw || typeof raw !== 'object') return null
  const obj = raw as Record<string, unknown>
  const task_id = obj.task_id
  const assignee_pid = obj.assignee_pid
  const event = obj.event
  if (typeof task_id !== 'string') return null
  if (typeof assignee_pid !== 'string') return null
  if (event !== 'assigned' && event !== 'reassigned') return null
  return { task_id, assignee_pid, event }
}

/** Strip the synthetic content prefix so the bare title shows up on
 * the card. Server format: ``<@user:{pid}> [TASK] {title}``.
 *
 * Since #275 the message also carries a multi-line self-instruction for
 * the assignee LLM beneath that first line. The instruction is meant
 * for the agent only; the chat card surface only ever displays the
 * canonical title, so we slice to the first line before stripping the
 * mention/marker prefixes.
 */
export function stripTaskMentionPrefix(content: string): string {
  const firstLine = content.split('\n', 1)[0] ?? ''
  return firstLine
    .replace(/^<@user:[^>]+>\s*/, '')
    .replace(/^\[TASK\]\s*/, '')
    .trim()
}

/**
 * Goal status notices (#957).
 *
 * When a goal needs a person or ends, the server posts one system message
 * carrying a ``goal_status`` block in its ``metadata``. The chat renders
 * it as a compact card instead of a bubble.
 */

export const GOAL_NOTICE_STATUSES = [
  'blocked',
  'plateau',
  'awaiting_confirmation',
  'achieved',
  'deadline_reached',
  'budget_exhausted',
  'stopped',
] as const

export type GoalNoticeStatus = (typeof GOAL_NOTICE_STATUSES)[number]

export interface GoalStatusMeta {
  campaign_id: string
  title: string
  status: GoalNoticeStatus
  detail: string | null
}

export function parseGoalStatus(metadata: unknown): GoalStatusMeta | null {
  if (!metadata || typeof metadata !== 'object') return null
  const raw = (metadata as Record<string, unknown>).goal_status
  if (!raw || typeof raw !== 'object') return null
  const obj = raw as Record<string, unknown>
  const { campaign_id, title, status, detail } = obj
  if (typeof campaign_id !== 'string' || typeof title !== 'string') return null
  if (!GOAL_NOTICE_STATUSES.includes(status as GoalNoticeStatus)) return null
  return {
    campaign_id,
    title,
    status: status as GoalNoticeStatus,
    detail: typeof detail === 'string' && detail ? detail : null,
  }
}

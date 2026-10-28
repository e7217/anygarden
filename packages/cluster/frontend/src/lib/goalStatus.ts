/**
 * Goal status notices (#957).
 *
 * When a goal needs a person or ends, the server posts one system message
 * carrying a ``goal_status`` block in its ``metadata``. The chat renders
 * it as a compact card instead of a bubble.
 *
 * #964 — when the goal reached its target, ended on its own terms, needs a
 * person, or ran another block of cycles (``active``), the same message is
 * the lead's report request: ``goal_report`` names the reason and the lead,
 * the body is the lead's brief (not shown), and the cycle's files ride on
 * ``references``.
 */

export const GOAL_NOTICE_STATUSES = [
  'active',
  'blocked',
  'plateau',
  'awaiting_confirmation',
  'achieved',
  'deadline_reached',
  'budget_exhausted',
  'stopped',
] as const

export type GoalNoticeStatus = (typeof GOAL_NOTICE_STATUSES)[number]

export const GOAL_REPORT_REASONS = ['confirmation', 'ended', 'needs_person', 'progress'] as const

export type GoalReportReason = (typeof GOAL_REPORT_REASONS)[number]

export interface GoalReportMeta {
  reason: GoalReportReason
  leadParticipantId: string | null
}

export interface GoalStatusMeta {
  campaign_id: string
  title: string
  status: GoalNoticeStatus
  detail: string | null
  report: GoalReportMeta | null
}

function parseGoalReport(raw: unknown): GoalReportMeta | null {
  if (!raw || typeof raw !== 'object') return null
  const { reason, lead_participant_id: lead } = raw as Record<string, unknown>
  if (!GOAL_REPORT_REASONS.includes(reason as GoalReportReason)) return null
  return {
    reason: reason as GoalReportReason,
    leadParticipantId: typeof lead === 'string' && lead ? lead : null,
  }
}

export function parseGoalStatus(metadata: unknown): GoalStatusMeta | null {
  if (!metadata || typeof metadata !== 'object') return null
  const raw = (metadata as Record<string, unknown>).goal_status
  if (!raw || typeof raw !== 'object') return null
  const obj = raw as Record<string, unknown>
  const { campaign_id, title, status, detail } = obj
  if (typeof campaign_id !== 'string' || typeof title !== 'string') return null
  if (!GOAL_NOTICE_STATUSES.includes(status as GoalNoticeStatus)) return null
  const report = parseGoalReport((metadata as Record<string, unknown>).goal_report)
  // A running goal only appears as a progress report.
  if (status === 'active' && report === null) return null
  return {
    campaign_id,
    title,
    status: status as GoalNoticeStatus,
    detail: typeof detail === 'string' && detail ? detail : null,
    report,
  }
}

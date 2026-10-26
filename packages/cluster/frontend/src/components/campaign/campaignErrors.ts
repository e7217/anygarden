/**
 * Localized text for campaign API errors and ``status_detail`` (#930).
 *
 * The v1 envelope carries a stable ``code`` and, for rule failures, a
 * ``reason`` (``api/v1/campaigns.py``, ``campaigns/service.py``). Known
 * ones map to catalog keys; anything else falls back to the server's
 * message so nothing is hidden.
 */
import type { MessageKey } from '@/i18n/messages'
import { CampaignApiError } from '@/lib/campaigns'

type Translate = (key: MessageKey, values?: Record<string, string | number>) => string

const REASON_KEYS: Record<string, MessageKey> = {
  ROOM_LEAD_REQUIRED: 'campaigns.error.leadRequired',
  ROOM_ARCHIVED: 'campaigns.error.roomArchived',
  CAMPAIGN_CHANGED: 'campaigns.error.changed',
  CAMPAIGN_ENDED: 'campaigns.error.ended',
  ILLEGAL_TRANSITION: 'campaigns.error.illegalTransition',
  EXTEND_NOTHING: 'campaigns.error.extendNothing',
  INVALID_DEADLINE: 'campaigns.error.invalidDeadline',
  INVALID_BUDGET: 'campaigns.error.invalidBudget',
  EXTEND_DOES_NOT_REOPEN: 'campaigns.error.doesNotReopen',
}

/**
 * The message for *error* from a campaign write. *kind* picks the
 * fallback wording and what a bare 403 means: creating needs the room's
 * task-manage role, changing needs the campaign's owner or an admin.
 */
export function campaignErrorMessage(
  t: Translate,
  error: unknown,
  kind: 'create' | 'change',
  values: Record<string, string | number> = {},
): string {
  if (error instanceof CampaignApiError) {
    if (error.code === 'CAMPAIGN_EVALUATOR_FORBIDDEN') return t('campaigns.error.evaluatorForbidden')
    if (error.reason && REASON_KEYS[error.reason]) return t(REASON_KEYS[error.reason], values)
    if (error.code === 'CAMPAIGN_NOT_FOUND') return t('campaigns.error.notFound')
    if (error.status === 403) {
      return t(kind === 'create' ? 'campaigns.error.createForbidden' : 'campaigns.error.forbidden')
    }
  }
  const message = error instanceof Error ? error.message : String(error)
  return kind === 'create'
    ? t('campaigns.error.createFailed', { message })
    : t('campaigns.action.failed', { message })
}

/**
 * ``status_detail`` is free text the server writes when it blocks a
 * campaign (``campaigns/lifecycle.py``, ``campaigns/scheduler.py``). The
 * fixed sentences are matched here; unknown text is shown as is.
 */
export function campaignStatusDetail(t: Translate, detail: string): string {
  const text = detail.trim()
  if (text === 'The pinned evaluator files changed; restore them or update the pins.') {
    return t('campaigns.detail.pinMismatch')
  }
  if (text === "The campaign's agent is no longer a participant of the room.") {
    return t('campaigns.detail.agentNotInRoom')
  }
  const failures = /^The evaluation failed (\d+) times in a row; check the evaluator\.$/.exec(text)
  if (failures) return t('campaigns.detail.evalFailures', { count: Number(failures[1]) })
  const reported = /^The agent reported: ([\s\S]*?)\.(?: To unblock: ([\s\S]*))?$/.exec(text)
  if (reported) {
    return reported[2]
      ? t('campaigns.detail.agentReportedHint', { reason: reported[1], hint: reported[2] })
      : t('campaigns.detail.agentReported', { reason: reported[1] })
  }
  return detail
}

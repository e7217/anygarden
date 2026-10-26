/**
 * Form model for creating a campaign (#930 plan §3.9).
 *
 * The mode decides what "how it ends" means, and so which inputs show:
 *
 * | mode     | target   | deadline | how it ends                     |
 * |----------|----------|----------|---------------------------------|
 * | target   | required | optional | the target is reached           |
 * | optimize | hidden   | required | the deadline passes (best kept) |
 *
 * Trusted evaluation, the evaluator's working folder, time limit and
 * pinned files sit under "Advanced" with the server's defaults.
 */
import type { CampaignCreateInput, CampaignDirection, CampaignMode } from '@/lib/campaigns'
import type { MessageKey } from '@/i18n/messages'

export type FieldRule = 'required' | 'optional' | 'hidden'

export function campaignFormFields(mode: CampaignMode): { target: FieldRule; deadline: FieldRule } {
  return mode === 'target'
    ? { target: 'required', deadline: 'optional' }
    : { target: 'hidden', deadline: 'required' }
}

export interface CampaignDraft {
  mode: CampaignMode
  title: string
  purpose: string
  metricName: string
  direction: CampaignDirection
  target: string
  /** ``<input type="datetime-local">`` value, local time. */
  deadline: string
  command: string
  scoreKey: string
  budgetTokens: string
  cwd: string
  timeoutSec: string
  /** One path per line. */
  pinPaths: string
  trustedExternal: boolean
}

export const DEFAULT_BUDGET_TOKENS = 500_000

export function emptyCampaignDraft(): CampaignDraft {
  return {
    mode: 'target',
    title: '',
    purpose: '',
    metricName: '',
    direction: 'maximize',
    target: '',
    deadline: '',
    command: '',
    scoreKey: 'score',
    budgetTokens: String(DEFAULT_BUDGET_TOKENS),
    cwd: '.',
    timeoutSec: '600',
    pinPaths: '',
    trustedExternal: false,
  }
}

export type DraftField =
  | 'title' | 'purpose' | 'metricName' | 'target' | 'deadline'
  | 'command' | 'scoreKey' | 'budgetTokens' | 'cwd' | 'timeoutSec'

export type DraftErrors = Partial<Record<DraftField, MessageKey>>

const MAX_TIMEOUT_SEC = 6 * 3600

/** Check *draft* and build the request body the server accepts. */
export function buildCampaignInput(
  draft: CampaignDraft,
  now: Date = new Date(),
): { errors: DraftErrors; input: CampaignCreateInput | null } {
  const errors: DraftErrors = {}
  const rules = campaignFormFields(draft.mode)
  const required = (field: DraftField, value: string) => {
    if (!value.trim()) errors[field] = 'campaigns.form.required'
  }
  required('title', draft.title)
  required('purpose', draft.purpose)
  required('metricName', draft.metricName)
  required('command', draft.command)
  required('scoreKey', draft.scoreKey)
  required('cwd', draft.cwd)

  let target: number | undefined
  if (rules.target !== 'hidden') {
    if (!draft.target.trim()) {
      if (rules.target === 'required') errors.target = 'campaigns.form.required'
    } else {
      const value = Number(draft.target)
      if (!Number.isFinite(value)) errors.target = 'campaigns.form.number'
      else target = value
    }
  }

  let deadline: string | undefined
  if (rules.deadline !== 'hidden') {
    if (!draft.deadline) {
      if (rules.deadline === 'required') errors.deadline = 'campaigns.form.required'
    } else {
      const at = new Date(draft.deadline)
      if (Number.isNaN(at.getTime())) errors.deadline = 'campaigns.form.required'
      else if (at.getTime() <= now.getTime()) errors.deadline = 'campaigns.form.deadlinePast'
      else deadline = at.toISOString()
    }
  }

  const budget = Number(draft.budgetTokens)
  if (!Number.isInteger(budget) || budget <= 0) errors.budgetTokens = 'campaigns.form.positiveInteger'

  const timeout = Number(draft.timeoutSec)
  if (!Number.isInteger(timeout) || timeout < 1 || timeout > MAX_TIMEOUT_SEC) {
    errors.timeoutSec = 'campaigns.form.timeoutRange'
  }

  if (Object.keys(errors).length > 0) return { errors, input: null }

  const input: CampaignCreateInput = {
    title: draft.title.trim(),
    purpose: draft.purpose.trim(),
    mode: draft.mode,
    metric: {
      name: draft.metricName.trim(),
      direction: draft.direction,
      ...(target !== undefined ? { target } : {}),
    },
    evaluator: {
      command: draft.command.trim(),
      cwd: draft.cwd.trim(),
      timeout_sec: timeout,
      pin_paths: draft.pinPaths.split('\n').map(p => p.trim()).filter(Boolean),
      score_key: draft.scoreKey.trim(),
    },
    budget: { max_total_tokens: budget },
    ...(deadline ? { deadline_at: deadline } : {}),
    trusted_eval: draft.trustedExternal ? 'external' : 'none',
  }
  return { errors, input }
}

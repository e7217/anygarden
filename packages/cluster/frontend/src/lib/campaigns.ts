/**
 * REST client and types for metric-driven campaigns (#930).
 *
 * Mirrors ``api/v1/campaigns.py`` (``CampaignOut`` / ``CampaignAttemptOut``).
 * The server rejects unknown fields on every write, so the payload
 * builders below send only what the schema defines.
 */
import { apiFetch } from '@/lib/api'

export type CampaignMode = 'target' | 'optimize'
export type CampaignDirection = 'maximize' | 'minimize'
export type CampaignTrustedEval = 'none' | 'external'

export type CampaignStatus =
  | 'active'
  | 'paused'
  | 'blocked'
  | 'plateau'
  | 'awaiting_confirmation'
  | 'achieved'
  | 'deadline_reached'
  | 'budget_exhausted'
  | 'stopped'

export const OPEN_CAMPAIGN_STATUSES: ReadonlySet<CampaignStatus> = new Set([
  'active', 'paused', 'blocked', 'plateau', 'awaiting_confirmation',
])

export interface CampaignMetric {
  name: string
  direction: CampaignDirection
  target?: number | null
  min_delta?: number
}

export interface CampaignEvaluator {
  command: string
  cwd: string
  timeout_sec: number
  pin_paths: string[]
  score_key: string
}

/** The token budget is optional (#963): no ``max_total_tokens``, no limit. */
export interface CampaignBudget {
  max_total_tokens?: number | null
  retro_reserve_ratio?: number
}

export interface CampaignScoreRef {
  attempt_id: string
  cycle_no: number
  dev_score: number | null
  trusted_score: number | null
  verified: boolean
}

export interface CampaignOpenAttempt {
  attempt_id: string
  cycle_no: number
  kind: string
  eval_status: string
  task_id: string | null
}

export interface Campaign {
  id: string
  room_id: string
  owner_id: string | null
  agent_id: string | null
  title: string
  purpose: string
  mode: CampaignMode
  metric: CampaignMetric
  evaluator: CampaignEvaluator
  trusted_eval: CampaignTrustedEval
  budget: CampaignBudget
  /** Tokens cycles may spend; ``null`` without a token budget. */
  usable_tokens: number | null
  deadline_at: string | null
  status: CampaignStatus
  stop_reason: string | null
  status_detail: string | null
  cycles_run: number
  tokens_used: number
  next_cycle_at: string | null
  cycles_since_improvement: number
  consecutive_eval_failures: number
  retro_due: string | null
  state_revision: number
  created_at: string | null
  updated_at: string | null
  best: CampaignScoreRef | null
  final: CampaignScoreRef | null
  open_attempt: CampaignOpenAttempt | null
}

export interface CampaignAttempt {
  id: string
  campaign_id: string
  cycle_no: number
  kind: 'cycle' | 'retrospective' | string
  task_id: string | null
  opened_at: string | null
  closed_at: string | null
  agent_outcome: string | null
  hypothesis: string | null
  change_summary: string | null
  artifact_ref: string | null
  lessons: string | null
  eval_status: string
  dev_score: number | null
  valid: boolean | null
  trusted_score: number | null
  tokens: number | null
  outcome: string | null
}

export interface CampaignCreateInput {
  title: string
  purpose: string
  mode: CampaignMode
  metric: { name: string; direction: CampaignDirection; target?: number }
  evaluator: CampaignEvaluator
  /** ``{}`` for no token budget (the default). */
  budget: { max_total_tokens?: number }
  deadline_at?: string
  trusted_eval?: CampaignTrustedEval
}

export interface CampaignExtendInput {
  deadline_at?: string
  max_total_tokens?: number
}

/** An API failure with the v1 envelope's stable ``code`` / ``reason``. */
export class CampaignApiError extends Error {
  readonly status: number
  readonly code: string | null
  readonly reason: string | null

  constructor(message: string, status: number, code: string | null, reason: string | null) {
    super(message)
    this.name = 'CampaignApiError'
    this.status = status
    this.code = code
    this.reason = reason
  }
}

async function jsonOrThrow<T>(resp: Response, action: string): Promise<T> {
  if (!resp.ok) {
    let message = ''
    let code: string | null = null
    let reason: string | null = null
    try {
      const body = await resp.json()
      code = typeof body?.code === 'string' ? body.code : null
      reason = typeof body?.reason === 'string' ? body.reason : null
      if (typeof body?.message === 'string') message = body.message
      else if (typeof body?.detail === 'string') message = body.detail
      else if (Array.isArray(body?.detail) && typeof body.detail[0]?.msg === 'string') message = body.detail[0].msg
    } catch {
      /* best effort */
    }
    throw new CampaignApiError(message || `${action} failed (HTTP ${resp.status})`, resp.status, code, reason)
  }
  return resp.json() as Promise<T>
}

const post = (url: string, body?: unknown) =>
  apiFetch(url, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })

export async function listRoomCampaigns(roomId: string): Promise<Campaign[]> {
  return jsonOrThrow(await apiFetch(`/api/v1/rooms/${roomId}/campaigns`), 'List campaigns')
}

export async function createCampaign(roomId: string, input: CampaignCreateInput): Promise<Campaign> {
  return jsonOrThrow(await post(`/api/v1/rooms/${roomId}/campaigns`, input), 'Create campaign')
}

export async function listCampaignAttempts(
  campaignId: string,
  limit = 5,
): Promise<{ items: CampaignAttempt[]; total: number; next_offset: number | null }> {
  return jsonOrThrow(
    await apiFetch(`/api/v1/campaigns/${campaignId}/attempts?limit=${limit}`),
    'List campaign attempts',
  )
}

export type CampaignAction = 'pause' | 'resume' | 'stop' | 'confirm' | 'reject'

export async function runCampaignAction(campaignId: string, action: CampaignAction): Promise<Campaign> {
  return jsonOrThrow(await post(`/api/v1/campaigns/${campaignId}/${action}`), `Campaign ${action}`)
}

export async function extendCampaign(campaignId: string, input: CampaignExtendInput): Promise<Campaign> {
  return jsonOrThrow(await post(`/api/v1/campaigns/${campaignId}/extend`, input), 'Extend campaign')
}

/** Which actions each status allows (``campaigns/service.py`` transitions). */
export function campaignActions(status: CampaignStatus): {
  pause: boolean
  resume: boolean
  stop: boolean
  decide: boolean
  extend: boolean
} {
  return {
    pause: status === 'active',
    resume: status === 'paused' || status === 'blocked' || status === 'plateau',
    stop: OPEN_CAMPAIGN_STATUSES.has(status),
    decide: status === 'awaiting_confirmation',
    extend: status === 'deadline_reached' || status === 'budget_exhausted',
  }
}

export type CampaignTone = 'neutral' | 'warning' | 'success'

/**
 * Badge tone per status (DESIGN.md §1: teal marks interaction, never
 * health; green/amber/red mark success, caution and errors).
 *
 * - running and every ending that is not a success read as neutral;
 * - a loop that waits on a person (paused, blocked, plateau, awaiting
 *   confirmation) reads as caution;
 * - only ``achieved`` reads as success.
 *
 * The badge always carries the status text, so colour is never the
 * only signal.
 */
export function campaignStatusTone(status: CampaignStatus): CampaignTone {
  switch (status) {
    case 'paused':
    case 'blocked':
    case 'plateau':
    case 'awaiting_confirmation':
      return 'warning'
    case 'achieved':
      return 'success'
    default:
      return 'neutral'
  }
}

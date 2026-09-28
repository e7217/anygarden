import type { Turn } from './ActivityPanel'

// #716 — the process indicator (Online/offline) says whether the agent is
// connected; this says whether its latest turn actually produced an answer.

export type TurnHealthStatus = 'none' | 'succeeded' | 'failed' | 'in_flight' | 'cancelled'
export type TurnFailureCategory = 'model_connection' | 'policy' | 'timeout' | 'busy' | 'engine'

export interface TurnHealth {
  status: TurnHealthStatus
  turn: Turn | null
  at: Date | null
  category: TurnFailureCategory | null
  // Closed, redacted code for the summary; free-form text never surfaces here.
  code: string | null
}

const MODEL_CONNECTION_CODES = new Set([
  'ENGINE_AUTH_ERROR',
  'PI_PROVIDER_ERROR',
  'AUTH_MISSING',
  'UNKNOWN_PROVIDER',
  'AUTH_CHECK_FAILED',
])

/** The leading closed code of a reported error, or null for free-form text. */
export function summaryErrorCode(error: string | null): string | null {
  if (!error) return null
  // UNSUPPORTED_RUNTIME carries a non-secret "observed vs required" suffix (#687).
  if (error.startsWith('UNSUPPORTED_RUNTIME:')) return 'UNSUPPORTED_RUNTIME'
  // Receipt codes are UPPER_SNAKE or lower_snake with an underscore; a bare
  // word or anything with spaces/punctuation is engine text, not a code.
  return /^(?:[A-Z][A-Z0-9]*|[a-z][a-z0-9]*)(?:_[A-Za-z0-9]+)+$/.test(error) ? error : null
}

export function classifyTurnFailure(error: string | null, outcome: string | null): TurnFailureCategory {
  const code = summaryErrorCode(error)
  if (code && MODEL_CONNECTION_CODES.has(code)) return 'model_connection'
  if (code === 'POLICY_DENIED') return 'policy'
  if (code === 'TIMEOUT_STOPPED' || outcome === 'timeout') return 'timeout'
  if (outcome === 'rejected') return 'busy'
  return 'engine'
}

/** Health of the newest turn; ``turns`` is ordered newest first (splitLogs). */
export function latestTurnHealth(turns: Turn[]): TurnHealth {
  // #720 — a turn the agent declined (not addressed to it) says nothing
  // about whether it can answer; judge the newest turn it actually took.
  const turn = turns.find(t => t.finalOutcome !== 'skipped')
  if (!turn) return { status: 'none', turn: null, at: null, category: null, code: null }
  const at = new Date(turn.lastTs)
  const base = { turn, at, category: null, code: null }
  const outcome = turn.finalOutcome
  if (outcome === 'ok') return { ...base, status: 'succeeded' }
  if (outcome === 'cancelled') return { ...base, status: 'cancelled' }
  if (outcome === 'queued' || outcome === 'retrying' || (!outcome && turn.outcome === 'in_flight')) {
    return { ...base, status: 'in_flight' }
  }
  if (!outcome && turn.outcome === 'responded') return { ...base, status: 'succeeded' }
  // failed / timeout / rejected / retry_exhausted, orphaned or silent turns.
  return {
    ...base,
    status: 'failed',
    category: classifyTurnFailure(turn.error, outcome),
    code: summaryErrorCode(turn.error),
  }
}

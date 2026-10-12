import { useCallback, useEffect, useState } from 'react'
import { apiFetch } from '@/lib/api'

/**
 * USE-07 — admin token-budget policies (`/api/v1/budgets`).
 *
 * Wraps the admin CRUD, the overview read model (observed usage per
 * policy, open incidents, budget-paused agents) and the agent resume
 * action. Mutations re-fetch so the screen always reflects the server.
 */

export type BudgetScopeType = 'global' | 'agent' | 'room'
export type BudgetWindowKind = 'rolling_24h' | 'calendar_day_utc'

export interface BudgetPolicy {
  id: string
  scope_type: BudgetScopeType
  scope_id: string | null
  token_ceiling: number
  warn_percent: number
  window_kind: BudgetWindowKind
  hard_stop_enabled: boolean
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface BudgetPolicyCreate {
  scope_type: BudgetScopeType
  scope_id?: string | null
  token_ceiling: number
  warn_percent?: number
  window_kind?: BudgetWindowKind
  hard_stop_enabled?: boolean
  is_active?: boolean
}

/** Scope is immutable after creation — only the tunables change. */
export type BudgetPolicyUpdate = Partial<
  Pick<BudgetPolicy, 'token_ceiling' | 'warn_percent' | 'window_kind' | 'hard_stop_enabled' | 'is_active'>
>

export interface BudgetIncident {
  id: string
  policy_id: string
  scope_type: BudgetScopeType
  scope_id: string | null
  threshold_type: 'soft' | 'hard'
  status: string
  observed_tokens: number
  window_start: string
  created_at: string
}

export interface BudgetOverview {
  usage: { policy_id: string; observed_tokens: number; window_start: string }[]
  incidents: BudgetIncident[]
  paused_agents: { agent_id: string; name: string }[]
}

export type BudgetLoadStatus = 'idle' | 'loading' | 'loaded' | 'error'

/** Error carrying the HTTP status and the server's `detail` when present. */
export class BudgetRequestError extends Error {
  status: number
  constructor(status: number, detail: string) {
    super(detail)
    this.status = status
  }
}

async function failure(resp: Response): Promise<BudgetRequestError> {
  let detail = ''
  try {
    const body = await resp.json()
    if (typeof body?.detail === 'string') detail = body.detail
    else if (Array.isArray(body?.detail)) detail = body.detail.map((d: { msg?: string }) => d.msg).filter(Boolean).join('; ')
  } catch { /* non-JSON body */ }
  return new BudgetRequestError(resp.status, detail)
}

const EMPTY_OVERVIEW: BudgetOverview = { usage: [], incidents: [], paused_agents: [] }

export function useBudgets({ enabled = true }: { enabled?: boolean } = {}) {
  const [policies, setPolicies] = useState<BudgetPolicy[]>([])
  const [overview, setOverview] = useState<BudgetOverview>(EMPTY_OVERVIEW)
  const [status, setStatus] = useState<BudgetLoadStatus>('idle')
  const [error, setError] = useState<BudgetRequestError | null>(null)

  const refresh = useCallback(async () => {
    setStatus('loading')
    try {
      const [policiesResp, overviewResp] = await Promise.all([
        apiFetch('/api/v1/budgets'),
        apiFetch('/api/v1/budgets/overview'),
      ])
      if (!policiesResp.ok) throw await failure(policiesResp)
      setPolicies(await policiesResp.json())
      // The overview is supplementary: a failure there must not hide the
      // policies the admin can still manage.
      setOverview(overviewResp.ok ? await overviewResp.json() : EMPTY_OVERVIEW)
      setError(null)
      setStatus('loaded')
    } catch (err) {
      setError(err instanceof BudgetRequestError ? err : new BudgetRequestError(0, String(err)))
      setStatus('error')
    }
  }, [])

  useEffect(() => {
    if (enabled) void refresh()
  }, [enabled, refresh])

  const createPolicy = useCallback(async (body: BudgetPolicyCreate) => {
    const resp = await apiFetch('/api/v1/budgets', { method: 'POST', body: JSON.stringify(body) })
    if (!resp.ok) throw await failure(resp)
    const created: BudgetPolicy = await resp.json()
    await refresh()
    return created
  }, [refresh])

  const updatePolicy = useCallback(async (id: string, body: BudgetPolicyUpdate) => {
    const resp = await apiFetch(`/api/v1/budgets/${encodeURIComponent(id)}`, {
      method: 'PATCH', body: JSON.stringify(body),
    })
    if (!resp.ok) throw await failure(resp)
    const updated: BudgetPolicy = await resp.json()
    await refresh()
    return updated
  }, [refresh])

  const deletePolicy = useCallback(async (id: string) => {
    const resp = await apiFetch(`/api/v1/budgets/${encodeURIComponent(id)}`, { method: 'DELETE' })
    if (!resp.ok && resp.status !== 204) throw await failure(resp)
    await refresh()
  }, [refresh])

  const resumeAgent = useCallback(async (agentId: string) => {
    const resp = await apiFetch(`/api/v1/budgets/agents/${encodeURIComponent(agentId)}/resume`, { method: 'POST' })
    if (!resp.ok) throw await failure(resp)
    const result: { agent_id: string; incidents_resolved: number } = await resp.json()
    await refresh()
    return result
  }, [refresh])

  return {
    policies, overview, status, error,
    refresh, createPolicy, updatePolicy, deletePolicy, resumeAgent,
  }
}

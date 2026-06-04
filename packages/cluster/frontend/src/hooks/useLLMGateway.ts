/**
 * Hooks for the LLM gateway admin REST surface (#197 Phase 4).
 *
 * Four independent slices — models / secrets / status / usage — each
 * tracks its own fetch status and exposes mutation helpers that do
 * optimistic local updates before calling ``refresh()`` in the
 * background. Mirrors the ``useMachines`` / ``useAgents`` conventions
 * already used elsewhere in the app; no React Query dependency added.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { apiFetch } from '@/lib/api'

// ── Types ────────────────────────────────────────────────────────────

export interface GatewayModel {
  id: string
  model_name: string
  provider: string
  upstream_model: string
  api_key_ref: string
  extra_params: Record<string, unknown> | null
  enabled: boolean
  created_at: string
  updated_at: string
}

export interface ModelCreateInput {
  model_name: string
  provider: string
  upstream_model: string
  api_key_ref: string
  extra_params?: Record<string, unknown> | null
  enabled?: boolean
}

export interface ModelUpdateInput {
  model_name?: string
  provider?: string
  upstream_model?: string
  api_key_ref?: string
  extra_params?: Record<string, unknown> | null
  enabled?: boolean
}

export interface GatewaySecret {
  env_var_name: string
  value_preview: string
  last_tested_at: string | null
  last_test_status: string | null
  created_at: string
  updated_at: string
}

export interface GatewayStatus {
  state: string
  pid: number | null
  port: number | null
  crash_count: number
  last_error: string | null
  config_hash: string | null
}

export interface TestResult {
  ok: boolean
  status_code: number | null
  duration_ms: number
  error: string | null
}

export interface OllamaModelsResult {
  ok: boolean
  models: string[]
  error: string | null
}

export interface UsageBucket {
  key: string
  request_count: number
  prompt_tokens: number
  completion_tokens: number
}

export interface UsageReport {
  window_hours: number
  total_requests: number
  by_model: UsageBucket[]
  by_agent: UsageBucket[]
}

export type LoadStatus = 'idle' | 'loading' | 'loaded' | 'error'

// ── Models ───────────────────────────────────────────────────────────

export function useGatewayModels() {
  const [models, setModels] = useState<GatewayModel[]>([])
  const [status, setStatus] = useState<LoadStatus>('idle')
  const [error, setError] = useState<string | null>(null)
  // Ref mirror so mutation callbacks that update state don't force
  // downstream re-renders of every caller through their closure deps.
  const statusRef = useRef<LoadStatus>('idle')
  useEffect(() => { statusRef.current = status }, [status])

  const fetchAll = useCallback(async () => {
    setStatus('loading')
    try {
      const resp = await apiFetch('/api/v1/llm-gateway/models')
      if (!resp.ok) throw new Error(`Failed to load models (${resp.status})`)
      const data: GatewayModel[] = await resp.json()
      data.sort((a, b) => a.model_name.localeCompare(b.model_name))
      setModels(data)
      setStatus('loaded')
      setError(null)
    } catch (err) {
      setStatus('error')
      setError(err instanceof Error ? err.message : String(err))
    }
  }, [])

  useEffect(() => { fetchAll() }, [fetchAll])

  const create = useCallback(
    async (input: ModelCreateInput): Promise<GatewayModel> => {
      const resp = await apiFetch('/api/v1/llm-gateway/models', {
        method: 'POST',
        body: JSON.stringify(input),
      })
      if (!resp.ok) {
        const detail = await readErrorDetail(resp)
        throw new Error(detail)
      }
      const row: GatewayModel = await resp.json()
      setModels(prev => [...prev, row].sort((a, b) =>
        a.model_name.localeCompare(b.model_name)
      ))
      return row
    },
    []
  )

  const update = useCallback(
    async (id: string, input: ModelUpdateInput): Promise<GatewayModel> => {
      const resp = await apiFetch(`/api/v1/llm-gateway/models/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(input),
      })
      if (!resp.ok) {
        const detail = await readErrorDetail(resp)
        throw new Error(detail)
      }
      const row: GatewayModel = await resp.json()
      setModels(prev => prev.map(m => (m.id === id ? row : m)))
      return row
    },
    []
  )

  const remove = useCallback(
    async (id: string): Promise<void> => {
      const resp = await apiFetch(`/api/v1/llm-gateway/models/${id}`, {
        method: 'DELETE',
      })
      if (!resp.ok) {
        const detail = await readErrorDetail(resp)
        throw new Error(detail)
      }
      setModels(prev => prev.filter(m => m.id !== id))
    },
    []
  )

  const test = useCallback(
    async (id: string): Promise<TestResult> => {
      const resp = await apiFetch(
        `/api/v1/llm-gateway/models/${id}/test`,
        { method: 'POST' }
      )
      // Even a non-2xx should produce a structured response on this
      // endpoint — the handler wraps upstream failures in ``ok=false``.
      if (!resp.ok && resp.status !== 200) {
        const detail = await readErrorDetail(resp)
        throw new Error(detail)
      }
      return (await resp.json()) as TestResult
    },
    []
  )

  return {
    models,
    status,
    error,
    refresh: fetchAll,
    create,
    update,
    remove,
    test,
  }
}

/**
 * List models installed on an Ollama instance (#410).
 *
 * Standalone (not part of useGatewayModels) because ModelDialog drives it
 * with its own local state and shouldn't trigger the hook's model-list
 * fetch. Backend-proxied — see POST /api/v1/llm-gateway/ollama/models.
 * Returns a structured result (ok=false on a reachable-but-failed probe);
 * only a hard non-200 (e.g. 403) throws.
 */
export async function fetchOllamaModels(
  apiBase: string
): Promise<OllamaModelsResult> {
  const resp = await apiFetch('/api/v1/llm-gateway/ollama/models', {
    method: 'POST',
    body: JSON.stringify({ api_base: apiBase || null }),
  })
  if (!resp.ok && resp.status !== 200) {
    const detail = await readErrorDetail(resp)
    throw new Error(detail)
  }
  return (await resp.json()) as OllamaModelsResult
}

// ── Secrets ──────────────────────────────────────────────────────────

export function useGatewaySecrets() {
  const [secrets, setSecrets] = useState<GatewaySecret[]>([])
  const [status, setStatus] = useState<LoadStatus>('idle')
  const [error, setError] = useState<string | null>(null)

  const fetchAll = useCallback(async () => {
    setStatus('loading')
    try {
      const resp = await apiFetch('/api/v1/llm-gateway/secrets')
      if (!resp.ok) throw new Error(`Failed to load secrets (${resp.status})`)
      const data: GatewaySecret[] = await resp.json()
      data.sort((a, b) => a.env_var_name.localeCompare(b.env_var_name))
      setSecrets(data)
      setStatus('loaded')
      setError(null)
    } catch (err) {
      setStatus('error')
      setError(err instanceof Error ? err.message : String(err))
    }
  }, [])

  useEffect(() => { fetchAll() }, [fetchAll])

  const create = useCallback(
    async (env_var_name: string, value: string): Promise<GatewaySecret> => {
      const resp = await apiFetch('/api/v1/llm-gateway/secrets', {
        method: 'POST',
        body: JSON.stringify({ env_var_name, value }),
      })
      if (!resp.ok) {
        const detail = await readErrorDetail(resp)
        throw new Error(detail)
      }
      const row: GatewaySecret = await resp.json()
      setSecrets(prev => [...prev, row].sort((a, b) =>
        a.env_var_name.localeCompare(b.env_var_name)
      ))
      return row
    },
    []
  )

  const update = useCallback(
    async (env_var_name: string, value: string): Promise<GatewaySecret> => {
      const resp = await apiFetch(
        `/api/v1/llm-gateway/secrets/${encodeURIComponent(env_var_name)}`,
        {
          method: 'PATCH',
          body: JSON.stringify({ value }),
        }
      )
      if (!resp.ok) {
        const detail = await readErrorDetail(resp)
        throw new Error(detail)
      }
      const row: GatewaySecret = await resp.json()
      setSecrets(prev =>
        prev.map(s => (s.env_var_name === env_var_name ? row : s))
      )
      return row
    },
    []
  )

  const remove = useCallback(
    async (env_var_name: string): Promise<void> => {
      const resp = await apiFetch(
        `/api/v1/llm-gateway/secrets/${encodeURIComponent(env_var_name)}`,
        { method: 'DELETE' }
      )
      if (!resp.ok) {
        const detail = await readErrorDetail(resp)
        throw new Error(detail)
      }
      setSecrets(prev => prev.filter(s => s.env_var_name !== env_var_name))
    },
    []
  )

  return { secrets, status, error, refresh: fetchAll, create, update, remove }
}

// ── Status / Apply / Restart ────────────────────────────────────────

export function useGatewayStatus(pollMs: number = 0) {
  const [status, setStatus] = useState<GatewayStatus | null>(null)
  const [loadState, setLoadState] = useState<LoadStatus>('idle')
  const [error, setError] = useState<string | null>(null)

  const fetchNow = useCallback(async () => {
    setLoadState('loading')
    try {
      const resp = await apiFetch('/api/v1/llm-gateway/status')
      if (resp.status === 503) {
        // Gateway disabled — not an error, just a known shape.
        setStatus(null)
        setLoadState('loaded')
        setError('Gateway is not enabled')
        return
      }
      if (!resp.ok) throw new Error(`Failed to load status (${resp.status})`)
      const data: GatewayStatus = await resp.json()
      setStatus(data)
      setLoadState('loaded')
      setError(null)
    } catch (err) {
      setLoadState('error')
      setError(err instanceof Error ? err.message : String(err))
    }
  }, [])

  useEffect(() => { fetchNow() }, [fetchNow])

  useEffect(() => {
    if (pollMs <= 0) return
    const id = setInterval(() => { fetchNow() }, pollMs)
    return () => clearInterval(id)
  }, [pollMs, fetchNow])

  const apply = useCallback(async (): Promise<GatewayStatus | null> => {
    const resp = await apiFetch('/api/v1/llm-gateway/apply', { method: 'POST' })
    if (!resp.ok) {
      const detail = await readErrorDetail(resp)
      throw new Error(detail)
    }
    const data: GatewayStatus = await resp.json()
    setStatus(data)
    return data
  }, [])

  const restart = useCallback(async (): Promise<GatewayStatus | null> => {
    const resp = await apiFetch(
      '/api/v1/llm-gateway/restart', { method: 'POST' }
    )
    if (!resp.ok) {
      const detail = await readErrorDetail(resp)
      throw new Error(detail)
    }
    const data: GatewayStatus = await resp.json()
    setStatus(data)
    return data
  }, [])

  return { status, loadState, error, refresh: fetchNow, apply, restart }
}

// ── Usage ────────────────────────────────────────────────────────────

export function useGatewayUsage(window: string = '24h') {
  const [usage, setUsage] = useState<UsageReport | null>(null)
  const [status, setStatus] = useState<LoadStatus>('idle')
  const [error, setError] = useState<string | null>(null)

  const fetchNow = useCallback(async () => {
    setStatus('loading')
    try {
      const resp = await apiFetch(
        `/api/v1/llm-gateway/usage?window=${encodeURIComponent(window)}`
      )
      if (!resp.ok) throw new Error(`Failed to load usage (${resp.status})`)
      const data: UsageReport = await resp.json()
      setUsage(data)
      setStatus('loaded')
      setError(null)
    } catch (err) {
      setStatus('error')
      setError(err instanceof Error ? err.message : String(err))
    }
  }, [window])

  useEffect(() => { fetchNow() }, [fetchNow])

  return { usage, status, error, refresh: fetchNow }
}

// ── Helpers ──────────────────────────────────────────────────────────

async function readErrorDetail(resp: Response): Promise<string> {
  try {
    const body = await resp.json()
    if (body && typeof body.detail === 'string') return body.detail
    return JSON.stringify(body)
  } catch {
    return `HTTP ${resp.status}`
  }
}

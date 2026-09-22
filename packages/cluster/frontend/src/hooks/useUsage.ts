import { useCallback, useEffect, useState } from 'react'
import { apiFetch } from '@/lib/api'

export interface UsageBucket {
  key: string
  request_count: number
  prompt_tokens: number
  completion_tokens: number
  cost_usd: number
}

export interface UsageReport {
  window_hours: number
  total_requests: number
  total_cost_usd: number
  by_model: UsageBucket[]
  by_agent: UsageBucket[]
}

export type LoadStatus = 'idle' | 'loading' | 'loaded' | 'error'

export function useUsage(window: string = '24h') {
  const [usage, setUsage] = useState<UsageReport | null>(null)
  const [status, setStatus] = useState<LoadStatus>('idle')
  const [error, setError] = useState<string | null>(null)

  const fetchNow = useCallback(async () => {
    setStatus('loading')
    try {
      const resp = await apiFetch(
        `/api/v1/usage?window=${encodeURIComponent(window)}`
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

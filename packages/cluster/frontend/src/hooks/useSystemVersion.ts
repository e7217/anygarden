import { useCallback, useEffect, useState } from 'react'
import { apiFetch } from '@/lib/api'

/**
 * The running server version (#546). Any logged-in user may read it;
 * a failure leaves ``version`` null and the UI simply omits it.
 */
export function useSystemVersion(): string | null {
  const [version, setVersion] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    apiFetch('/api/v1/system/version')
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!cancelled && data?.version) setVersion(data.version)
      })
      .catch(() => {
        /* version display is best-effort — never surface an error */
      })
    return () => {
      cancelled = true
    }
  }, [])

  return version
}

export interface PackageUpdate {
  package: string
  current: string
  latest: string | null
  update_available: boolean
  checked_at: string | null
  error: string | null
}

/**
 * How often an open tab re-reads the cached ``/updates`` (SYS-06). The
 * server's background check refreshes that cache (default daily), so a
 * modest poll is enough for a long-lived tab to notice a new release.
 */
export const UPDATE_POLL_MS = 30 * 60 * 1000

/**
 * Admin-only update status (#546). Reads the cached ``/updates`` (no
 * outbound call) on mount and every ``UPDATE_POLL_MS`` (SYS-06 — the
 * server fills the cache in the background); ``refresh`` triggers a live
 * PyPI check via ``/check-updates``. ``enabled`` gates loading so
 * non-admin callers never hit the admin endpoint.
 */
export function useUpdateStatus(enabled: boolean) {
  const [updates, setUpdates] = useState<PackageUpdate[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const r = await apiFetch('/api/v1/system/updates')
      if (r.ok) setUpdates(await r.json())
    } catch {
      /* cached read is best-effort */
    }
  }, [])

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const r = await apiFetch('/api/v1/system/check-updates', { method: 'POST' })
      if (r.ok) {
        setUpdates(await r.json())
      } else {
        setError(`Check failed (${r.status})`)
      }
    } catch {
      setError('Check failed — network error')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!enabled) return
    load()
    const timer = setInterval(load, UPDATE_POLL_MS)
    return () => clearInterval(timer)
  }, [enabled, load])

  return { updates, loading, error, refresh }
}

import { useCallback, useEffect, useRef, useState } from 'react'
import type { GraphResponse, Scope } from './types'

interface UseGraphDataState {
  data: GraphResponse | null
  loading: boolean
  error: Error | null
  refresh: () => void
}

/**
 * SWR-lite: fetches ``/api/v1/graph`` with ETag support.
 *
 * - On first mount, does a plain GET.
 * - On ``refresh()``, sends the last-seen ETag via ``If-None-Match``.
 *   A 304 response means the in-memory ``data`` is still current and we
 *   simply clear ``loading`` without touching state.
 * - Aborts the in-flight request on unmount to avoid setState-after-unmount.
 * - Returns the explicitly-typed ``GraphResponse`` so downstream hooks
 *   can rely on the stable backend shape.
 *
 * @param scope ``personal | global | auto`` — forwarded to the backend.
 * @param pollInterval Optional milliseconds between background
 *   re-fetches. Defaults to off (mount + manual refresh only). When
 *   set, polls are paused while the tab is hidden
 *   (``document.visibilityState === 'hidden'``) and resume on the next
 *   ``visibilitychange`` event — keeps idle background tabs from
 *   hammering the backend even though ETag short-circuits to 304.
 */
export function useGraphData(
  scope: Scope = 'auto',
  pollInterval?: number,
): UseGraphDataState {
  const [data, setData] = useState<GraphResponse | null>(null)
  const [loading, setLoading] = useState<boolean>(true)
  const [error, setError] = useState<Error | null>(null)
  const etagRef = useRef<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  // Mirror of ``loading`` that timer callbacks can read synchronously
  // without re-subscribing. Keeps the polling effect from racing an
  // in-flight request: if a fetch is still outstanding when the next
  // tick fires, we skip it rather than aborting and restarting (which
  // would livelock on networks slower than ``pollInterval``).
  const loadingRef = useRef<boolean>(true)
  // Bumping ``trigger`` schedules a refetch without rearming the initial
  // effect dependency graph — keeps the hook stable under React 18
  // double-invoke in dev mode.
  const [trigger, setTrigger] = useState(0)

  const refresh = useCallback(() => {
    setTrigger(t => t + 1)
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    abortRef.current?.abort()
    abortRef.current = controller

    setLoading(true)
    loadingRef.current = true
    setError(null)

    const token = localStorage.getItem('anygarden_token')
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    }
    if (token) headers['Authorization'] = `Bearer ${token}`
    if (etagRef.current) headers['If-None-Match'] = etagRef.current

    fetch(`/api/v1/graph?scope=${scope}`, {
      headers,
      signal: controller.signal,
    })
      .then(async resp => {
        if (resp.status === 304) {
          // In-memory copy still authoritative — nothing to do.
          return
        }
        if (!resp.ok) {
          const body = await resp.text().catch(() => '')
          throw new Error(
            `Graph fetch failed (${resp.status}): ${body || resp.statusText}`,
          )
        }
        const etag = resp.headers.get('etag')
        if (etag) etagRef.current = etag
        const payload = (await resp.json()) as GraphResponse
        setData(payload)
      })
      .catch(err => {
        if (controller.signal.aborted) return
        setError(err instanceof Error ? err : new Error(String(err)))
      })
      .finally(() => {
        if (controller.signal.aborted) return
        setLoading(false)
        loadingRef.current = false
      })

    return () => {
      controller.abort()
    }
  }, [scope, trigger])

  // Optional background polling. Re-runs when ``pollInterval`` changes
  // so callers can dial it dynamically (e.g. lower frequency on slow
  // networks). The handler is stable: every tick simply bumps the same
  // ``trigger`` counter the manual refresh path uses, so the fetch
  // logic above stays the single source of truth (one place to add
  // headers, abort, parse, set state).
  useEffect(() => {
    if (!pollInterval || pollInterval <= 0) return

    let intervalId: ReturnType<typeof setInterval> | null = null

    const start = () => {
      if (intervalId !== null) return
      intervalId = setInterval(() => {
        // Belt-and-suspenders: even if the visibilitychange listener
        // misses an event (some embedded contexts skip them), skip
        // the tick when the tab is hidden.
        if (
          typeof document !== 'undefined' &&
          document.visibilityState === 'hidden'
        ) {
          return
        }
        // Livelock guard: if a previous fetch is still in flight,
        // bumping ``trigger`` would abort it via the main effect and
        // restart from scratch. On networks slower than
        // ``pollInterval`` that yields a stream of aborted requests
        // that never settle. Skipping the tick lets the outstanding
        // request finish and the next tick picks up a fresh fetch.
        if (loadingRef.current) return
        refresh()
      }, pollInterval)
    }
    const stop = () => {
      if (intervalId !== null) {
        clearInterval(intervalId)
        intervalId = null
      }
    }

    if (
      typeof document === 'undefined' ||
      document.visibilityState !== 'hidden'
    ) {
      start()
    }

    const onVisibility = () => {
      if (typeof document === 'undefined') return
      if (document.visibilityState === 'hidden') {
        stop()
      } else {
        // Refresh immediately on tab return so the user sees fresh
        // data without waiting up to ``pollInterval`` for the next
        // tick. Restart the interval afterwards so subsequent ticks
        // stay on schedule.
        refresh()
        start()
      }
    }
    if (typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', onVisibility)
    }

    return () => {
      stop()
      if (typeof document !== 'undefined') {
        document.removeEventListener('visibilitychange', onVisibility)
      }
    }
  }, [pollInterval, refresh])

  return { data, loading, error, refresh }
}

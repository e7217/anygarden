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

export type UpdateStatus = ReturnType<typeof useUpdateStatus>

/** ``GET /api/v1/system/node-update`` (#927). */
export interface NodeUpdateState {
  supported: boolean
  /** not_integrated_node | unsupported_platform | development_install | unsupported_install */
  reason: string | null
  status: 'idle' | 'installing' | 'restarting' | 'failed'
  from_version: string
  target_version: string | null
  error: string | null
}

/**
 * What the panel shows. ``updated``/``stalled``/``same_version`` exist only
 * in the browser: the node's own state is in memory and starts ``idle``
 * after it re-executes, so the outcome is read from the version it reports.
 */
export type NodeUpdatePhase =
  | 'idle'
  | 'installing'
  | 'restarting'
  | 'failed'
  | 'updated'
  | 'same_version'
  | 'stalled'

export interface NodeUpdateProgress {
  phase: NodeUpdatePhase
  /** Version the node ran when the update started. */
  fromVersion: string | null
  targetVersion: string | null
  /** Version the node came back on (``updated``). */
  version: string | null
  error: string | null
  /** When the node last answered (ms epoch); the stall clock starts here. */
  since: number
}

/** A node that has not come back after this long needs its terminal checked. */
export const NODE_RESTART_STALL_MS = 3 * 60 * 1000
export const NODE_UPDATE_POLL_MS = 2000

const BUSY: NodeUpdatePhase[] = ['installing', 'restarting']

/**
 * Fold one poll into the progress. ``server`` is null when the poll failed —
 * expected while the node restarts.
 */
export function nextNodeUpdateProgress(
  prev: NodeUpdateProgress,
  server: NodeUpdateState | null,
  now: number,
): NodeUpdateProgress {
  const busy = BUSY.includes(prev.phase)
  if (server === null) {
    if (!busy) return prev
    // Unreachable while installing means the node is already going down.
    if (now - prev.since > NODE_RESTART_STALL_MS) return { ...prev, phase: 'stalled' }
    return { ...prev, phase: 'restarting' }
  }
  if (server.status !== 'idle') {
    return {
      phase: server.status,
      fromVersion: server.from_version,
      targetVersion: server.target_version,
      version: null,
      error: server.error,
      since: now,
    }
  }
  if (busy || prev.phase === 'stalled') {
    // Back after a restart: the reported version tells whether it worked.
    if (prev.fromVersion !== null && server.from_version === prev.fromVersion) {
      return { ...prev, phase: 'same_version', version: server.from_version }
    }
    return { ...prev, phase: 'updated', version: server.from_version, error: null }
  }
  return prev
}

const INITIAL_PROGRESS: NodeUpdateProgress = {
  phase: 'idle',
  fromVersion: null,
  targetVersion: null,
  version: null,
  error: null,
  since: 0,
}

/**
 * Update this integrated node in place (#927): install, restart, and follow
 * the node through its restart by polling until it reports a version.
 */
export function useNodeUpdate(enabled: boolean) {
  const [state, setState] = useState<NodeUpdateState | null>(null)
  const [progress, setProgress] = useState<NodeUpdateProgress>(INITIAL_PROGRESS)
  const [startError, setStartError] = useState<string | null>(null)

  const poll = useCallback(async () => {
    let server: NodeUpdateState | null = null
    try {
      const r = await apiFetch('/api/v1/system/node-update')
      const body = r.ok ? await r.json() : null
      if (typeof body?.status === 'string') server = body
    } catch {
      /* the node is restarting */
    }
    if (server) setState(server)
    setProgress((prev) => nextNodeUpdateProgress(prev, server, Date.now()))
  }, [])

  useEffect(() => {
    if (!enabled) return
    poll()
  }, [enabled, poll])

  const active = progress.phase === 'installing' || progress.phase === 'restarting'
  useEffect(() => {
    if (!enabled || !active) return
    const timer = setInterval(poll, NODE_UPDATE_POLL_MS)
    return () => clearInterval(timer)
  }, [enabled, active, poll])

  const start = useCallback(async (targetVersion: string) => {
    setStartError(null)
    try {
      const r = await apiFetch('/api/v1/system/node-update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_version: targetVersion }),
      })
      const body = await r.json().catch(() => null)
      if (!r.ok) {
        setStartError(body?.message ?? `Update failed (${r.status})`)
        return
      }
      setState(body)
      setProgress((prev) => nextNodeUpdateProgress(prev, body, Date.now()))
    } catch {
      setStartError('network')
    }
  }, [])

  return { state, progress, startError, start }
}

export type NodeUpdateControl = ReturnType<typeof useNodeUpdate>

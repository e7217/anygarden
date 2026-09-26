import { useCallback, useEffect, useRef, useState } from 'react'
import { apiFetch } from '@/lib/api'

export interface ActivityLog {
  id: string
  event_type: string
  timestamp: string
  request_id: string | null
  agent_id?: string | null
  details: Record<string, unknown> | null
}
interface Cursor { timestamp: string; id: string }
type Operation = 'initial' | 'refresh' | 'older'
interface Snapshot {
  agentId: string | null
  logs: ActivityLog[]
  loaded: boolean
  pending: Operation | null
  error: Operation | null
  hasMore: boolean
  olderCursor: Cursor | null
  catchupCursor: Cursor | null
}
const PAGE_SIZE = 50
const UPDATE_SIZE = 200
const MAX_UPDATE_PAGES = 5
export const ACTIVITY_POLL_MS = 5000
const empty = (agentId: string | null): Snapshot => ({ agentId, logs: [], loaded: false, pending: null, error: null, hasMore: false, olderCursor: null, catchupCursor: null })

function mergeRows(previous: ActivityLog[], incoming: ActivityLog[]): ActivityLog[] {
  const rows = new Map(previous.map(row => [row.id, row]))
  for (const row of incoming) rows.set(row.id, row)
  // API timestamps use UTC with six fractional digits, preserving microseconds.
  return [...rows.values()].sort((a, b) => a.timestamp === b.timestamp
    ? (a.id < b.id ? 1 : a.id > b.id ? -1 : 0)
    : (a.timestamp < b.timestamp ? 1 : -1))
}
export function isPendingOutcome(outcome: unknown): boolean {
  return outcome === 'queued' || outcome === 'retrying'
}
export function isTerminalActivity(row: ActivityLog): boolean {
  if (row.event_type === 'handler_orphaned') return true
  if (row.event_type !== 'handler_finished') return false
  const outcome = row.details?.outcome
  // A missing outcome is the historical handler_finished contract.
  return outcome == null || ['ok', 'failed', 'timeout', 'cancelled', 'rejected', 'retry_exhausted'].includes(String(outcome))
}
export function hasPendingActivity(logs: ActivityLog[]): boolean {
  const pending = new Set<string>()
  const finished = new Set<string>()
  for (const row of logs) {
    if (!row.request_id) continue
    if (isTerminalActivity(row)) finished.add(row.request_id)
    if (['message_received', 'handler_started', 'engine_call_started'].includes(row.event_type)
      || (row.event_type === 'handler_finished' && isPendingOutcome(row.details?.outcome))) pending.add(row.request_id)
  }
  return [...pending].some(id => !finished.has(id))
}

/** Cursor-backed event history. Old pages stay visible while new events catch up. */
export function useAgentActivity(agentId: string | null, active = true) {
  const [snapshot, setSnapshot] = useState<Snapshot>(() => empty(agentId))
  const snapshotRef = useRef(snapshot)
  const generation = useRef(0)
  const mounted = useRef(false)
  const inFlight = useRef<AbortController | null>(null)
  const [visible, setVisible] = useState(() => document.visibilityState !== 'hidden')
  const publish = useCallback((next: Snapshot) => { snapshotRef.current = next; setSnapshot(next) }, [])

  const load = useCallback(async (operation: Operation) => {
    if (!mounted.current || !agentId || !active || inFlight.current || snapshotRef.current.agentId !== agentId) return
    const context = generation.current
    const controller = new AbortController()
    inFlight.current = controller
    const valid = () => mounted.current && generation.current === context && !controller.signal.aborted
    const current = snapshotRef.current.agentId === agentId ? snapshotRef.current : empty(agentId)
    publish({ ...current, pending: operation, error: null })
    async function page(direction?: 'before' | 'after', cursor?: Cursor, limit = PAGE_SIZE + 1): Promise<ActivityLog[]> {
      const query = new URLSearchParams({ limit: String(limit) })
      if (direction && cursor) {
        query.set(`${direction}_timestamp`, cursor.timestamp)
        query.set(`${direction}_id`, cursor.id)
      }
      const response = await apiFetch(`/api/v1/agents/${encodeURIComponent(agentId!)}/activity?${query}`, { signal: controller.signal })
      if (!valid()) throw new Error('obsolete activity request')
      if (!response.ok) throw new Error('activity request failed')
      const rows: unknown = await response.json()
      if (!valid()) throw new Error('obsolete activity response')
      if (!Array.isArray(rows)) throw new Error('invalid activity response')
      return rows as ActivityLog[]
    }
    try {
      if (operation === 'refresh' && current.logs.length > 0) {
        // Replay the latest timestamp to include late same-time IDs. Further
        // pages use an exclusive full cursor, so large batches always advance.
        let cursor = current.catchupCursor ?? { timestamp: current.logs[0].timestamp, id: '' }
        for (let index = 0; index < MAX_UPDATE_PAGES; index++) {
          const rows = await page('after', cursor, UPDATE_SIZE)
          if (!valid()) return
          const continuing = rows.length === UPDATE_SIZE
          const last = rows.at(-1)
          if (last) cursor = { timestamp: last.timestamp, id: last.id }
          publish({ ...snapshotRef.current, loaded: true, logs: mergeRows(snapshotRef.current.logs, rows), catchupCursor: continuing ? cursor : null })
          if (!continuing) break
        }
      } else {
        const older = operation === 'older' && current.olderCursor
        const rows = await page(older ? 'before' : undefined, older || undefined)
        if (!valid()) return
        const shown = rows.slice(0, PAGE_SIZE)
        const last = shown.at(-1)
        publish({ ...snapshotRef.current, loaded: true, logs: mergeRows(older ? snapshotRef.current.logs : [], shown), hasMore: rows.length > PAGE_SIZE, olderCursor: last ? { timestamp: last.timestamp, id: last.id } : null, catchupCursor: older ? current.catchupCursor : null })
      }
    } catch {
      if (valid()) publish({ ...snapshotRef.current, error: operation })
    } finally {
      if (valid()) {
        inFlight.current = null
        publish({ ...snapshotRef.current, pending: null })
      }
    }
  }, [agentId, active, publish])

  useEffect(() => {
    generation.current += 1
    mounted.current = true
    inFlight.current?.abort()
    inFlight.current = null
    if (snapshotRef.current.agentId !== agentId) publish(empty(agentId))
    else if (snapshotRef.current.pending) publish({ ...snapshotRef.current, pending: null })
    if (active && agentId) void load(snapshotRef.current.loaded ? 'refresh' : 'initial')
    return () => {
      mounted.current = false
      generation.current += 1
      inFlight.current?.abort()
      inFlight.current = null
    }
  }, [agentId, active, load, publish])

  useEffect(() => {
    const changed = () => setVisible(document.visibilityState !== 'hidden')
    document.addEventListener('visibilitychange', changed)
    return () => document.removeEventListener('visibilitychange', changed)
  }, [])

  const state = snapshot.agentId === agentId ? snapshot : empty(agentId)
  const updating = hasPendingActivity(state.logs) || Boolean(state.catchupCursor)
  useEffect(() => {
    if (!active || !visible || !updating || state.pending || state.error) return
    const timer = window.setTimeout(() => { void load('refresh') }, ACTIVITY_POLL_MS)
    return () => window.clearTimeout(timer)
  }, [active, visible, updating, state.pending, state.error, state.logs, load])

  return {
    logs: state.logs,
    loading: Boolean(agentId && active && !state.loaded && !state.error),
    refreshing: state.pending === 'refresh',
    loadingMore: state.pending === 'older',
    busy: Boolean(state.pending),
    error: state.error,
    hasMore: state.hasMore,
    autoUpdating: active && visible && updating && !state.error,
    refresh: () => load(state.loaded ? 'refresh' : 'initial'),
    loadMore: () => state.hasMore ? load('older') : Promise.resolve(),
    retry: () => load(state.error ?? (state.loaded ? 'refresh' : 'initial')),
  }
}

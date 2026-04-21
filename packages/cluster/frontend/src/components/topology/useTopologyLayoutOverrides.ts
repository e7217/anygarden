import { useCallback, useEffect, useMemo, useState } from 'react'

/**
 * Per-user, per-scope topology node position overrides (#234).
 *
 * Storage layer only: loads/saves a ``{ [nodeId]: { x, y } }`` map to
 * localStorage under ``doorae_topology_layout_v1_${userId}_${scope}``.
 * The caller (``TopologyPage``) feeds the result into ``useGraphLayout``
 * so dagre-computed positions are overlaid with the user's manual edits.
 *
 * When ``userId`` or ``scope`` is null (pre-login, loading) the hook
 * becomes a no-op that returns an empty map and silent setters, so
 * callers don't need to guard every invocation.
 *
 * localStorage access is wrapped in try/catch — Safari private mode
 * throws on ``setItem``. Same policy as ``useSidebarLayout``: degrade
 * to in-memory state for the session instead of surfacing an error.
 */

export type Scope = 'global' | 'personal'
export type Overrides = Record<string, { x: number; y: number }>

export interface LayoutOverridesApi {
  overrides: Overrides
  setPosition: (nodeId: string, pos: { x: number; y: number }) => void
  reset: () => void
  hasOverrides: boolean
}

const STORAGE_PREFIX = 'doorae_topology_layout_v1'

function keyFor(userId: string, scope: Scope): string {
  return `${STORAGE_PREFIX}_${userId}_${scope}`
}

function readOverrides(key: string): Overrides {
  try {
    const raw = localStorage.getItem(key)
    if (!raw) return {}
    const parsed = JSON.parse(raw) as unknown
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return parsed as Overrides
    }
  } catch {
    /* corrupted JSON or storage disabled — fall through */
  }
  return {}
}

function writeOverrides(key: string, value: Overrides): void {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    /* ignore */
  }
}

function removeOverrides(key: string): void {
  try {
    localStorage.removeItem(key)
  } catch {
    /* ignore */
  }
}

export function useTopologyLayoutOverrides(
  userId: string | null,
  scope: Scope | null,
): LayoutOverridesApi {
  const key = userId && scope ? keyFor(userId, scope) : null

  const [overrides, setOverrides] = useState<Overrides>(() =>
    key ? readOverrides(key) : {},
  )

  // When the storage key changes (scope switch, login/logout), re-hydrate
  // from the new key so the user sees their own overrides — not whatever
  // was loaded on mount for a stale (user, scope) pair.
  useEffect(() => {
    setOverrides(key ? readOverrides(key) : {})
  }, [key])

  const setPosition = useCallback(
    (nodeId: string, pos: { x: number; y: number }) => {
      if (!key) return
      setOverrides(prev => {
        const next: Overrides = { ...prev, [nodeId]: { x: pos.x, y: pos.y } }
        writeOverrides(key, next)
        return next
      })
    },
    [key],
  )

  const reset = useCallback(() => {
    if (key) removeOverrides(key)
    setOverrides({})
  }, [key])

  const hasOverrides = useMemo(
    () => Object.keys(overrides).length > 0,
    [overrides],
  )

  return { overrides, setPosition, reset, hasOverrides }
}

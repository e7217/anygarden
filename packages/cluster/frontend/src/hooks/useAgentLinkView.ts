import { useCallback, useMemo, useState } from 'react'
import type { Viewport, XYPosition } from '@xyflow/react'

export interface AgentLinkView {
  positions: Record<string, XYPosition>
  viewport?: Viewport
}
const empty = (): AgentLinkView => ({ positions: {} })
const point = (value: unknown): value is XYPosition => {
  const p = value as XYPosition | null
  return !!p && typeof p.x === 'number' && Number.isFinite(p.x) && typeof p.y === 'number' && Number.isFinite(p.y)
}
function read(key: string | null): AgentLinkView {
  if (!key) return empty()
  try {
    const raw = JSON.parse(localStorage.getItem(key) ?? 'null') as AgentLinkView | null
    if (!raw || typeof raw !== 'object') return empty()
    const positions = Object.fromEntries(Object.entries(raw.positions ?? {}).filter(([, position]) => point(position)))
    const viewport = point(raw.viewport) && Number.isFinite(raw.viewport?.zoom) && raw.viewport!.zoom >= 0.3 && raw.viewport!.zoom <= 2 ? raw.viewport : undefined
    return { positions, viewport }
  } catch { return empty() }
}
/** Presentation only: no relationships, conversations or credentials in storage. */
export function useAgentLinkView(userId: string | null) {
  const key = userId ? `anygarden_agent_links_view_v1_${userId}` : null
  const initial = useMemo(() => read(key), [key])
  const [state, setState] = useState({ key, view: initial })
  const view = state.key === key ? state.view : initial
  const change = useCallback((update: (previous: AgentLinkView) => AgentLinkView) => {
    if (!key) return
    setState(previous => {
      const next = update(previous.key === key ? previous.view : initial)
      try { localStorage.setItem(key, JSON.stringify(next)) } catch { /* Keep this view in memory. */ }
      return { key, view: next }
    })
  }, [key, initial])
  const savePosition = useCallback((id: string, position: XYPosition) => {
    if (point(position)) change(previous => ({ ...previous, positions: { ...previous.positions, [id]: position } }))
  }, [change])
  const saveViewport = useCallback((viewport: Viewport) => {
    if (point(viewport) && Number.isFinite(viewport.zoom) && viewport.zoom >= 0.3 && viewport.zoom <= 2) change(previous => ({ ...previous, viewport }))
  }, [change])
  const reset = useCallback(() => change(empty), [change])
  return { view, savePosition, saveViewport, reset }
}

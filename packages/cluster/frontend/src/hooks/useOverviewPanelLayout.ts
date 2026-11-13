import { useCallback, useEffect, useState } from 'react'
import { RIGHT_RAIL_WIDTH, clampRailWidth } from '@/hooks/useRightSidebarLayout'

// Side panel of the overview page (#1042). Same width bounds as the chat
// rail (#760) but its own storage keys: folding the rail in a room must not
// hide the overview's summary, and the overview starts open.
const COLLAPSED_KEY = 'anygarden_overview_panel_collapsed'
const WIDTH_KEY = 'anygarden_overview_panel_width'

function read(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

function write(key: string, value: string): void {
  try {
    localStorage.setItem(key, value)
  } catch {
    /* ignore */
  }
}

function readWidth(): number {
  const raw = read(WIDTH_KEY)
  const parsed = raw === null ? NaN : Number(raw)
  return Number.isFinite(parsed) ? clampRailWidth(parsed) : RIGHT_RAIL_WIDTH.default
}

export function useOverviewPanelLayout() {
  const [collapsed, setCollapsed] = useState(() => read(COLLAPSED_KEY) === 'true')
  const [width, setWidthState] = useState(readWidth)

  useEffect(() => write(COLLAPSED_KEY, String(collapsed)), [collapsed])
  useEffect(() => write(WIDTH_KEY, String(width)), [width])

  const toggleCollapsed = useCallback(() => setCollapsed(prev => !prev), [])
  const setWidth = useCallback((px: number) => setWidthState(clampRailWidth(px)), [])
  const resetWidth = useCallback(() => setWidthState(RIGHT_RAIL_WIDTH.default), [])

  return { collapsed, setCollapsed, toggleCollapsed, width, setWidth, resetWidth }
}

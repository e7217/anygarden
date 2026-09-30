import {
  createContext,
  createElement,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'

// Right context rail collapse state (#302, #329). Mirrors
// useSidebarLayout (#117) for the storage/persist contract; the
// no-localStorage default is viewport-driven so the rail expands by
// default on lg+ screens (where there is room for both sidebar and
// rail) and collapses by default below 1024px (where the conversation
// otherwise gets squeezed). Persisted user choice always wins.
const STORAGE_KEY = 'anygarden_right_sidebar_collapsed'
const WIDTH_STORAGE_KEY = 'anygarden_right_sidebar_width'
const LG_BREAKPOINT_QUERY = '(min-width: 1024px)'

// Desktop width of the right-hand slot (#760), shared by the context
// rail and the thread panel so swapping one for the other never moves
// the chat column. The default matches the pre-#760 ``lg:w-80``.
export const RIGHT_RAIL_WIDTH = { default: 320, min: 280, max: 560, step: 16 } as const

export function clampRailWidth(px: number): number {
  return Math.round(Math.min(RIGHT_RAIL_WIDTH.max, Math.max(RIGHT_RAIL_WIDTH.min, px)))
}

export interface RightSidebarLayoutValue {
  /** Desktop-only collapsed flag. Mobile (< md) handles overlay drawer
   *  state separately and does not read this. */
  collapsed: boolean
  /** Toggle + persist. */
  toggleCollapsed: () => void
  /** Force a specific value. Used by deep-link flows (e.g. clicking a
   *  task in AgentSettingsDialog auto-opens the rail in the destination
   *  room) and by tests. */
  setCollapsed: (next: boolean) => void
  /** Desktop width in px, clamped to ``RIGHT_RAIL_WIDTH``. */
  width: number
  /** Clamp + persist. Callers commit once per gesture, not per frame. */
  setWidth: (px: number) => void
  resetWidth: () => void
}

const RightSidebarLayoutContext =
  createContext<RightSidebarLayoutValue | null>(null)

function readInitial(): boolean {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw !== null) return raw === 'true'
    // No persisted preference — fall back to viewport policy. ≥1024px
    // (lg) starts expanded; below that the rail is collapsed so the
    // conversation isn't squeezed (#329).
    if (typeof window !== 'undefined' && typeof window.matchMedia === 'function') {
      return !window.matchMedia(LG_BREAKPOINT_QUERY).matches
    }
    return true
  } catch {
    return true
  }
}

function readInitialWidth(): number {
  try {
    const raw = localStorage.getItem(WIDTH_STORAGE_KEY)
    const parsed = raw === null ? NaN : Number(raw)
    return Number.isFinite(parsed) ? clampRailWidth(parsed) : RIGHT_RAIL_WIDTH.default
  } catch {
    return RIGHT_RAIL_WIDTH.default
  }
}

export function RightSidebarLayoutProvider({ children }: { children: ReactNode }) {
  const [collapsed, setCollapsedState] = useState<boolean>(() => readInitial())
  const [width, setWidthState] = useState<number>(() => readInitialWidth())

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, String(collapsed))
    } catch {
      /* ignore */
    }
  }, [collapsed])

  useEffect(() => {
    try {
      localStorage.setItem(WIDTH_STORAGE_KEY, String(width))
    } catch {
      /* ignore */
    }
  }, [width])

  const setWidth = useCallback((px: number) => {
    setWidthState(clampRailWidth(px))
  }, [])

  const resetWidth = useCallback(() => {
    setWidthState(RIGHT_RAIL_WIDTH.default)
  }, [])

  const setCollapsed = useCallback((next: boolean) => {
    setCollapsedState(next)
  }, [])

  const toggleCollapsed = useCallback(() => {
    setCollapsedState(prev => !prev)
  }, [])

  const value = useMemo<RightSidebarLayoutValue>(
    () => ({ collapsed, toggleCollapsed, setCollapsed, width, setWidth, resetWidth }),
    [collapsed, toggleCollapsed, setCollapsed, width, setWidth, resetWidth],
  )

  return createElement(RightSidebarLayoutContext.Provider, { value }, children)
}

export function useRightSidebarLayout(): RightSidebarLayoutValue {
  const ctx = useContext(RightSidebarLayoutContext)
  if (ctx === null) {
    throw new Error(
      'useRightSidebarLayout() must be called inside <RightSidebarLayoutProvider>. ' +
        'Wrap the app root in src/App.tsx.',
    )
  }
  return ctx
}

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

// Persisted desktop sidebar collapse state (#106/#115). Pre-#115 this
// lived inside ChatPage; hoisting it here so AdminMachinesPage and
// TopologyPage can share the same toggle, Ctrl/Cmd+B shortcut, and
// localStorage-backed user preference without prop drilling.
const STORAGE_KEY = 'anygarden_sidebar_collapsed'
const WIDTH_STORAGE_KEY = 'anygarden_sidebar_width'

// Desktop sidebar width (#967). The default is the pre-#967 ``md:w-64``;
// the chat column's minimum width, not ``max``, is what stops a wide
// sidebar from crowding the conversation on a narrow screen.
export const SIDEBAR_WIDTH = { default: 256, min: 208, max: 480, step: 16 } as const

export function clampSidebarWidth(px: number): number {
  return Math.round(Math.min(SIDEBAR_WIDTH.max, Math.max(SIDEBAR_WIDTH.min, px)))
}

export interface SidebarLayoutValue {
  /** Desktop-only collapsed flag. Mobile (< md) ignores this entirely. */
  collapsed: boolean
  /** Toggle + persist. Safe to pass as a keydown handler target. */
  toggleCollapsed: () => void
  /** Force a specific value. Currently unused in-tree but kept for
   *  future callers that need imperative control (e.g. a settings
   *  screen reset). */
  setCollapsed: (next: boolean) => void
  /** Desktop width in px, clamped to ``SIDEBAR_WIDTH``. Kept apart from
   *  ``collapsed`` so expanding restores the last width. */
  width: number
  /** Clamp + persist. Callers commit once per gesture, not per frame. */
  setWidth: (px: number) => void
  resetWidth: () => void
}

const SidebarLayoutContext = createContext<SidebarLayoutValue | null>(null)

function readInitial(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === 'true'
  } catch {
    // localStorage throws in private-mode Safari / disabled storage.
    // Same graceful-default the pre-#115 ChatPage code used.
    return false
  }
}

function readInitialWidth(): number {
  try {
    const raw = localStorage.getItem(WIDTH_STORAGE_KEY)
    const parsed = raw === null ? NaN : Number(raw)
    return Number.isFinite(parsed) ? clampSidebarWidth(parsed) : SIDEBAR_WIDTH.default
  } catch {
    return SIDEBAR_WIDTH.default
  }
}

export function SidebarLayoutProvider({ children }: { children: ReactNode }) {
  const [collapsed, setCollapsedState] = useState<boolean>(() => readInitial())
  const [width, setWidthState] = useState<number>(() => readInitialWidth())

  // Persist on every change. Mirrors the ``expandedProjects`` pattern
  // in useRooms.ts so the two sidebar-adjacent prefs behave the same.
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
    setWidthState(clampSidebarWidth(px))
  }, [])

  const resetWidth = useCallback(() => {
    setWidthState(SIDEBAR_WIDTH.default)
  }, [])

  const setCollapsed = useCallback((next: boolean) => {
    setCollapsedState(next)
  }, [])

  const toggleCollapsed = useCallback(() => {
    setCollapsedState(prev => !prev)
  }, [])

  const value = useMemo<SidebarLayoutValue>(
    () => ({ collapsed, toggleCollapsed, setCollapsed, width, setWidth, resetWidth }),
    [collapsed, toggleCollapsed, setCollapsed, width, setWidth, resetWidth],
  )

  // JSX deliberately avoided so this file stays ``.ts`` — matches
  // the useRooms.ts convention and keeps import shapes uniform.
  return createElement(SidebarLayoutContext.Provider, { value }, children)
}

export function useSidebarLayout(): SidebarLayoutValue {
  const ctx = useContext(SidebarLayoutContext)
  if (ctx === null) {
    throw new Error(
      'useSidebarLayout() must be called inside <SidebarLayoutProvider>. ' +
        'Wrap the app root in src/App.tsx.',
    )
  }
  return ctx
}

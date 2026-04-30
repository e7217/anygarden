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
const STORAGE_KEY = 'doorae_right_sidebar_collapsed'
const LG_BREAKPOINT_QUERY = '(min-width: 1024px)'

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

export function RightSidebarLayoutProvider({ children }: { children: ReactNode }) {
  const [collapsed, setCollapsedState] = useState<boolean>(() => readInitial())

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, String(collapsed))
    } catch {
      /* ignore */
    }
  }, [collapsed])

  const setCollapsed = useCallback((next: boolean) => {
    setCollapsedState(next)
  }, [])

  const toggleCollapsed = useCallback(() => {
    setCollapsedState(prev => !prev)
  }, [])

  const value = useMemo<RightSidebarLayoutValue>(
    () => ({ collapsed, toggleCollapsed, setCollapsed }),
    [collapsed, toggleCollapsed, setCollapsed],
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

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

// Right context rail collapse state (#302). Mirrors useSidebarLayout
// (#117) byte-for-byte except for the storage key and the inverted
// default — the right rail is *closed* by default to keep the chat
// canvas wide; users opt in to the rail by toggling it open.
const STORAGE_KEY = 'doorae_right_sidebar_collapsed'

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
    // Default *collapsed* — the inverted polarity vs. the left sidebar
    // is intentional. The chat canvas should win the available width
    // until the user explicitly opens the context rail.
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw === null) return true
    return raw === 'true'
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

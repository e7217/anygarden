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
const STORAGE_KEY = 'doorae_sidebar_collapsed'

export interface SidebarLayoutValue {
  /** Desktop-only collapsed flag. Mobile (< md) ignores this entirely. */
  collapsed: boolean
  /** Toggle + persist. Safe to pass as a keydown handler target. */
  toggleCollapsed: () => void
  /** Force a specific value. Currently unused in-tree but kept for
   *  future callers that need imperative control (e.g. a settings
   *  screen reset). */
  setCollapsed: (next: boolean) => void
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

export function SidebarLayoutProvider({ children }: { children: ReactNode }) {
  const [collapsed, setCollapsedState] = useState<boolean>(() => readInitial())

  // Persist on every change. Mirrors the ``expandedProjects`` pattern
  // in useRooms.ts so the two sidebar-adjacent prefs behave the same.
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

  const value = useMemo<SidebarLayoutValue>(
    () => ({ collapsed, toggleCollapsed, setCollapsed }),
    [collapsed, toggleCollapsed, setCollapsed],
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

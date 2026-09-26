import * as React from 'react'

export type Theme = 'light' | 'dark'

export const THEME_STORAGE_KEY = 'anygarden-theme'

type ThemeContextValue = {
  theme: Theme
  setTheme: (theme: Theme) => void
  toggleTheme: () => void
}

const ThemeContext = React.createContext<ThemeContextValue | null>(null)

function storedTheme(): Theme {
  if (typeof window === 'undefined') return 'light'

  const paintedTheme = document.documentElement.dataset.theme
  if (paintedTheme === 'light' || paintedTheme === 'dark') return paintedTheme

  try {
    return window.localStorage.getItem(THEME_STORAGE_KEY) === 'dark' ? 'dark' : 'light'
  } catch {
    return 'light'
  }
}

function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme
  document.documentElement.classList.toggle('dark', theme === 'dark')
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setTheme] = React.useState<Theme>(storedTheme)

  React.useEffect(() => {
    applyTheme(theme)
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, theme)
    } catch {
      // The visible theme still works when storage is unavailable.
    }
  }, [theme])

  React.useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key === THEME_STORAGE_KEY) {
        setTheme(event.newValue === 'dark' ? 'dark' : 'light')
      }
    }
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [])

  const toggleTheme = React.useCallback(() => {
    setTheme((current) => current === 'light' ? 'dark' : 'light')
  }, [])

  const value = React.useMemo(
    () => ({ theme, setTheme, toggleTheme }),
    [theme, toggleTheme],
  )

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

export function useTheme(): ThemeContextValue {
  const context = React.useContext(ThemeContext)
  if (!context) throw new Error('useTheme must be used within ThemeProvider')
  return context
}

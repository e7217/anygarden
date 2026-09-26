import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { en, ko, type MessageKey } from './messages'

export type Locale = 'ko' | 'en'
export const LOCALE_STORAGE_KEY = 'anygarden_locale'

function browserLocale(): Locale {
  return typeof navigator !== 'undefined' && navigator.language.toLowerCase().startsWith('ko') ? 'ko' : 'en'
}

export function readInitialLocale(): Locale {
  try {
    const stored = localStorage.getItem(LOCALE_STORAGE_KEY)
    if (stored === 'ko' || stored === 'en') return stored
  } catch { /* Storage may be disabled. */ }
  return browserLocale()
}

type Translate = (key: MessageKey, values?: Record<string, string | number>) => string

function translate(locale: Locale, key: MessageKey, values?: Record<string, string | number>): string {
  let result: string = (locale === 'ko' ? ko[key] : en[key]) ?? en[key]
  if (values) {
    for (const [name, value] of Object.entries(values)) {
      result = result.split(`{{${name}}}`).join(String(value))
    }
  }
  return result
}

interface LocaleContextValue {
  locale: Locale
  setLocale: (locale: Locale) => void
  t: Translate
  formatNumber: (value: number, options?: Intl.NumberFormatOptions) => string
  formatDate: (value: Date, options?: Intl.DateTimeFormatOptions) => string
}

// Standalone component previews and existing unit tests can render without the
// app root. The real application always installs LocaleProvider in main.tsx.
const standaloneLocale = typeof navigator === 'undefined' ? 'en' : browserLocale()
const LocaleContext = createContext<LocaleContextValue>({
  locale: standaloneLocale,
  setLocale: () => {},
  t: (key, values) => translate(standaloneLocale, key, values),
  formatNumber: (value, options) => new Intl.NumberFormat(standaloneLocale, options).format(value),
  formatDate: (value, options) => new Intl.DateTimeFormat(standaloneLocale, options).format(value),
})

export function LocaleProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(readInitialLocale)

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next)
    try { localStorage.setItem(LOCALE_STORAGE_KEY, next) } catch { /* Storage may be disabled. */ }
  }, [])

  useEffect(() => {
    document.documentElement.lang = locale
  }, [locale])

  const t = useCallback<Translate>((key, values) => translate(locale, key, values), [locale])

  const formatNumber = useCallback((value: number, options?: Intl.NumberFormatOptions) =>
    new Intl.NumberFormat(locale, options).format(value), [locale])
  const formatDate = useCallback((value: Date, options?: Intl.DateTimeFormatOptions) =>
    new Intl.DateTimeFormat(locale, options).format(value), [locale])

  const context = useMemo(() => ({ locale, setLocale, t, formatNumber, formatDate }),
    [locale, setLocale, t, formatNumber, formatDate])

  return <LocaleContext.Provider value={context}>{children}</LocaleContext.Provider>
}

export function useLocale(): LocaleContextValue {
  return useContext(LocaleContext)
}

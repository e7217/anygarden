import type { Locale } from './LocaleProvider'
import type { en } from './locales/en'

// Each locale is its own chunk (./locales/*), so startup downloads only the
// active language. App code imports `MessageKey` as a type and reads strings
// through useLocale(); importing a locale module statically would put that
// language back into the entry chunk.
export type MessageKey = keyof typeof en
export type Messages = Record<MessageKey, string>

const catalogs: Partial<Record<Locale, Messages>> = {}

/** Strings for a locale that has already been loaded, else undefined. */
export function messagesFor(locale: Locale): Messages | undefined {
  return catalogs[locale]
}

/** Makes a locale available synchronously (test setup registers both). */
export function registerMessages(locale: Locale, messages: Messages): void {
  catalogs[locale] = messages
}

/** Loads a locale's chunk once; later calls resolve from the cache. */
export async function loadMessages(locale: Locale): Promise<Messages> {
  const cached = catalogs[locale]
  if (cached) return cached
  const messages = locale === 'ko'
    ? (await import('./locales/ko')).ko
    : (await import('./locales/en')).en
  registerMessages(locale, messages)
  return messages
}

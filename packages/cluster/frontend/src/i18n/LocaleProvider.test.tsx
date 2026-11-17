// @vitest-environment jsdom
import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { LocaleProvider, LOCALE_STORAGE_KEY, useLocale } from './LocaleProvider'
import { en } from './locales/en'
import { ko } from './locales/ko'

beforeEach(() => localStorage.clear())
afterEach(() => localStorage.clear())

describe('LocaleProvider', () => {
  it('respects and persists an explicit language choice', () => {
    localStorage.setItem(LOCALE_STORAGE_KEY, 'en')
    const { result } = renderHook(() => useLocale(), { wrapper: LocaleProvider })
    expect(result.current.locale).toBe('en')

    act(() => result.current.setLocale('ko'))
    expect(result.current.locale).toBe('ko')
    expect(result.current.t('auth.login')).toBe('로그인')
    expect(document.documentElement.lang).toBe('ko')
    expect(localStorage.getItem(LOCALE_STORAGE_KEY)).toBe('ko')
  })

  it('keeps the English and Korean catalogues complete', () => {
    const { result } = renderHook(() => useLocale(), { wrapper: LocaleProvider })
    expect(result.current.t('common.lastSeen', { time: '1 minute ago' })).not.toContain('{{time}}')
  })

  it('gives both languages the same keys', () => {
    expect(Object.keys(ko).sort()).toEqual(Object.keys(en).sort())
  })

  it('loads a language that is not loaded yet, then swaps without a reload', async () => {
    // A fresh module graph has an empty catalogue registry, as in the app
    // before main.tsx loads anything.
    localStorage.setItem(LOCALE_STORAGE_KEY, 'en')
    vi.resetModules()
    const messages = await import('./messages')
    const provider = await import('./LocaleProvider')
    messages.registerMessages('en', en)
    expect(messages.messagesFor('ko')).toBeUndefined()

    const { result } = renderHook(() => provider.useLocale(), { wrapper: provider.LocaleProvider })
    act(() => result.current.setLocale('ko'))
    // Until the Korean chunk arrives the English copy stays on screen.
    expect(result.current.locale).toBe('en')
    expect(result.current.t('auth.login')).toBe(en['auth.login'])

    await waitFor(() => expect(result.current.locale).toBe('ko'))
    expect(result.current.t('auth.login')).toBe('로그인')
    expect(messages.messagesFor('ko')).toBe(await messages.loadMessages('ko'))
  })
})

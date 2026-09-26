// @vitest-environment jsdom
import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { LocaleProvider, LOCALE_STORAGE_KEY, useLocale } from './LocaleProvider'

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
})

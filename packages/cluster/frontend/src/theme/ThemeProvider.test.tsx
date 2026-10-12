// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { LocaleProvider, LOCALE_STORAGE_KEY } from '@/i18n/LocaleProvider'
import { ThemeProvider, THEME_STORAGE_KEY } from './ThemeProvider'
import { ThemeToggle } from './ThemeToggle'

beforeEach(() => {
  window.localStorage.clear()
  window.localStorage.setItem(LOCALE_STORAGE_KEY, 'en')
  document.documentElement.removeAttribute('data-theme')
  document.documentElement.classList.remove('dark')
})

afterEach(() => cleanup())

describe('theme selection', () => {
  it('starts in light mode and persists a dark selection [SYS-05]', async () => {
    render(<LocaleProvider><ThemeProvider><ThemeToggle /></ThemeProvider></LocaleProvider>)

    const toggle = screen.getByRole('button', { name: 'Dark mode' })
    expect(toggle).toHaveAttribute('aria-pressed', 'false')
    expect(document.documentElement).toHaveAttribute('data-theme', 'light')

    fireEvent.click(toggle)

    await waitFor(() => expect(document.documentElement).toHaveAttribute('data-theme', 'dark'))
    expect(document.documentElement).toHaveClass('dark')
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark')
    expect(toggle).toHaveAttribute('aria-pressed', 'true')
  })

  it('uses a saved selection on the next mount [SYS-05]', () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, 'dark')
    render(<LocaleProvider><ThemeProvider><ThemeToggle /></ThemeProvider></LocaleProvider>)

    expect(document.documentElement).toHaveAttribute('data-theme', 'dark')
    expect(screen.getByRole('button', { name: 'Dark mode' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('follows a theme change made in another tab [SYS-05]', async () => {
    render(<LocaleProvider><ThemeProvider><ThemeToggle /></ThemeProvider></LocaleProvider>)
    expect(document.documentElement).toHaveAttribute('data-theme', 'light')

    act(() => {
      window.dispatchEvent(new StorageEvent('storage', { key: THEME_STORAGE_KEY, newValue: 'dark' }))
    })
    await waitFor(() => expect(document.documentElement).toHaveAttribute('data-theme', 'dark'))
    expect(screen.getByRole('button', { name: 'Dark mode' })).toHaveAttribute('aria-pressed', 'true')

    // Unrelated keys are ignored.
    act(() => {
      window.dispatchEvent(new StorageEvent('storage', { key: 'other-key', newValue: 'light' }))
    })
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark')

    act(() => {
      window.dispatchEvent(new StorageEvent('storage', { key: THEME_STORAGE_KEY, newValue: 'light' }))
    })
    await waitFor(() => expect(document.documentElement).toHaveAttribute('data-theme', 'light'))
  })
})

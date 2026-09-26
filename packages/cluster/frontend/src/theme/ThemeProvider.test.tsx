// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
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
  it('starts in light mode and persists a dark selection', async () => {
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

  it('uses a saved selection on the next mount', () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, 'dark')
    render(<LocaleProvider><ThemeProvider><ThemeToggle /></ThemeProvider></LocaleProvider>)

    expect(document.documentElement).toHaveAttribute('data-theme', 'dark')
    expect(screen.getByRole('button', { name: 'Dark mode' })).toHaveAttribute('aria-pressed', 'true')
  })
})

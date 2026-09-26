// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { LocaleProvider, LOCALE_STORAGE_KEY } from '@/i18n/LocaleProvider'
import { ThemeProvider, THEME_STORAGE_KEY } from '@/theme/ThemeProvider'
import SidebarPreferencesMenu from './SidebarPreferencesMenu'

beforeEach(() => {
  localStorage.clear()
  localStorage.setItem(LOCALE_STORAGE_KEY, 'en')
  document.documentElement.removeAttribute('data-theme')
  document.documentElement.classList.remove('dark')
})
afterEach(cleanup)

function renderPreferences() {
  return render(
    <LocaleProvider>
      <ThemeProvider>
        <SidebarPreferencesMenu email="user@example.com" serverVersion="1.2.3" />
      </ThemeProvider>
    </LocaleProvider>,
  )
}

describe('SidebarPreferencesMenu', () => {
  it('lets an account change language and theme from the same menu and persists both', () => {
    renderPreferences()
    expect(screen.queryByRole('group', { name: 'Language' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Personal settings' }))
    expect(screen.getByRole('button', { name: 'English' })).toHaveFocus()
    expect(screen.getByText('Theme')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '한국어' }))
    expect(localStorage.getItem(LOCALE_STORAGE_KEY)).toBe('ko')
    expect(screen.getByRole('group', { name: '언어' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '다크 모드' }))
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark')
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark')
    expect(screen.getByRole('button', { name: '다크 모드' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: '개인 설정' })).toHaveAttribute('aria-expanded', 'true')
  })

  it('closes with Escape or an outside click and restores keyboard focus after Escape', () => {
    renderPreferences()
    const trigger = screen.getByRole('button', { name: 'Personal settings' })
    fireEvent.click(trigger)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(trigger).toHaveFocus()
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(trigger)
    fireEvent.pointerDown(document.body)
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
  })
})

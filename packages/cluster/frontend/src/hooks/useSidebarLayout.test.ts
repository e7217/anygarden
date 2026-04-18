// @vitest-environment jsdom
// Unit tests for the useSidebarLayout hook and SidebarLayoutProvider.
// Covers the same ground the pre-#115 ChatPage-local state covered —
// localStorage hydration, persistence across toggles, and the throw-
// when-unprovided guard that mirrors useRooms's Context discipline.
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import {
  SidebarLayoutProvider,
  useSidebarLayout,
} from './useSidebarLayout'

const STORAGE_KEY = 'doorae_sidebar_collapsed'

function wrap({ children }: { children: ReactNode }) {
  return createElement(SidebarLayoutProvider, null, children)
}

beforeEach(() => {
  localStorage.clear()
})

afterEach(() => {
  localStorage.clear()
})

describe('useSidebarLayout', () => {
  it('throws when called outside a SidebarLayoutProvider', () => {
    // Swallow React's error boundary noise — we want the assertion,
    // not the console spam that comes with an uncaught render throw.
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    try {
      expect(() => renderHook(() => useSidebarLayout())).toThrow(
        /SidebarLayoutProvider/,
      )
    } finally {
      spy.mockRestore()
    }
  })

  it('defaults collapsed=false when localStorage has no value', () => {
    const { result } = renderHook(() => useSidebarLayout(), { wrapper: wrap })
    expect(result.current.collapsed).toBe(false)
  })

  it('hydrates collapsed=true from localStorage on first render', () => {
    localStorage.setItem(STORAGE_KEY, 'true')
    const { result } = renderHook(() => useSidebarLayout(), { wrapper: wrap })
    expect(result.current.collapsed).toBe(true)
  })

  it('toggleCollapsed flips the boolean and persists it to localStorage', () => {
    const { result } = renderHook(() => useSidebarLayout(), { wrapper: wrap })
    expect(result.current.collapsed).toBe(false)

    act(() => { result.current.toggleCollapsed() })
    expect(result.current.collapsed).toBe(true)
    expect(localStorage.getItem(STORAGE_KEY)).toBe('true')

    act(() => { result.current.toggleCollapsed() })
    expect(result.current.collapsed).toBe(false)
    expect(localStorage.getItem(STORAGE_KEY)).toBe('false')
  })

  it('setCollapsed writes the exact value', () => {
    const { result } = renderHook(() => useSidebarLayout(), { wrapper: wrap })
    act(() => { result.current.setCollapsed(true) })
    expect(result.current.collapsed).toBe(true)
    expect(localStorage.getItem(STORAGE_KEY)).toBe('true')

    act(() => { result.current.setCollapsed(false) })
    expect(result.current.collapsed).toBe(false)
    expect(localStorage.getItem(STORAGE_KEY)).toBe('false')
  })
})

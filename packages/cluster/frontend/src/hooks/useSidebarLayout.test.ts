// @vitest-environment jsdom
// Unit tests for the useSidebarLayout hook and SidebarLayoutProvider.
// Covers the same ground the pre-#115 ChatPage-local state covered —
// localStorage hydration, persistence across toggles, and the throw-
// when-unprovided guard that mirrors useRooms's Context discipline.
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import {
  SIDEBAR_WIDTH,
  SidebarLayoutProvider,
  clampSidebarWidth,
  useSidebarLayout,
} from './useSidebarLayout'

const STORAGE_KEY = 'anygarden_sidebar_collapsed'
const WIDTH_KEY = 'anygarden_sidebar_width'

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
  })})

describe('useSidebarLayout width (#967)', () => {
  it('[ROOM-13] defaults to the pre-#967 w-64 width', () => {
    const { result } = renderHook(() => useSidebarLayout(), { wrapper: wrap })
    expect(result.current.width).toBe(256)
    expect(SIDEBAR_WIDTH.default).toBe(256)
  })

  it('hydrates a stored width and clamps one outside the range', () => {
    localStorage.setItem(WIDTH_KEY, '320')
    const first = renderHook(() => useSidebarLayout(), { wrapper: wrap })
    expect(first.result.current.width).toBe(320)
    first.unmount()

    localStorage.setItem(WIDTH_KEY, '5000')
    const second = renderHook(() => useSidebarLayout(), { wrapper: wrap })
    expect(second.result.current.width).toBe(SIDEBAR_WIDTH.max)
  })

  it('falls back to the default for a corrupt stored width', () => {
    localStorage.setItem(WIDTH_KEY, 'wide')
    const { result } = renderHook(() => useSidebarLayout(), { wrapper: wrap })
    expect(result.current.width).toBe(SIDEBAR_WIDTH.default)
  })

  it('[ROOM-13] setWidth clamps and persists; resetWidth restores the default', () => {
    const { result } = renderHook(() => useSidebarLayout(), { wrapper: wrap })
    act(() => { result.current.setWidth(100) })
    expect(result.current.width).toBe(SIDEBAR_WIDTH.min)
    act(() => { result.current.setWidth(333.4) })
    expect(result.current.width).toBe(333)
    expect(localStorage.getItem(WIDTH_KEY)).toBe('333')
    act(() => { result.current.resetWidth() })
    expect(result.current.width).toBe(SIDEBAR_WIDTH.default)
  })

  it('[ROOM-13] keeps the width across collapse and expand', () => {
    const { result } = renderHook(() => useSidebarLayout(), { wrapper: wrap })
    act(() => { result.current.setWidth(360) })
    act(() => { result.current.toggleCollapsed() })
    act(() => { result.current.toggleCollapsed() })
    expect(result.current.width).toBe(360)
  })

  it('clampSidebarWidth bounds both ends', () => {
    expect(clampSidebarWidth(0)).toBe(SIDEBAR_WIDTH.min)
    expect(clampSidebarWidth(10_000)).toBe(SIDEBAR_WIDTH.max)
  })
})

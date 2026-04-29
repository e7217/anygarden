// @vitest-environment jsdom
// Unit tests for the useRightSidebarLayout hook and
// RightSidebarLayoutProvider — a 1:1 mirror of useSidebarLayout (#117)
// for the right-side context rail (#302). Same hydrate/persist/throw
// contract; only the storage key and the default-policy differ.
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import {
  RightSidebarLayoutProvider,
  useRightSidebarLayout,
} from './useRightSidebarLayout'

const STORAGE_KEY = 'doorae_right_sidebar_collapsed'

function wrap({ children }: { children: ReactNode }) {
  return createElement(RightSidebarLayoutProvider, null, children)
}

// jsdom does not implement matchMedia. Each test that exercises the
// no-localStorage default needs a deterministic answer for the
// (min-width: 1024px) media query.
function mockMatchMedia(matches: boolean): void {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  })
}

beforeEach(() => {
  localStorage.clear()
  // Default to "lg viewport" so tests that don't care about the
  // viewport branch (hydrate/toggle/setCollapsed) get a stable
  // matchMedia. Tests that *do* care override per-case.
  mockMatchMedia(true)
})

afterEach(() => {
  localStorage.clear()
})

describe('useRightSidebarLayout', () => {
  it('throws when called outside a RightSidebarLayoutProvider', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    try {
      expect(() => renderHook(() => useRightSidebarLayout())).toThrow(
        /RightSidebarLayoutProvider/,
      )
    } finally {
      spy.mockRestore()
    }
  })

  it('defaults collapsed=false on lg+ viewport when localStorage has no value', () => {
    // #329 — at >=1024px the chat canvas already has room for both the
    // sidebar and the rail, so the rail starts open. Users still get
    // their persisted preference back if they ever toggled it.
    mockMatchMedia(true)
    const { result } = renderHook(() => useRightSidebarLayout(), { wrapper: wrap })
    expect(result.current.collapsed).toBe(false)
  })

  it('defaults collapsed=true on sub-lg viewport when localStorage has no value', () => {
    // #329 — under 1024px the conversation needs the width more than
    // the context rail does, so default to collapsed. The previous
    // policy (collapsed regardless of viewport) is preserved here for
    // narrow screens.
    mockMatchMedia(false)
    const { result } = renderHook(() => useRightSidebarLayout(), { wrapper: wrap })
    expect(result.current.collapsed).toBe(true)
  })

  it('hydrates collapsed=false from localStorage on first render', () => {
    localStorage.setItem(STORAGE_KEY, 'false')
    // Even when the viewport policy would say "collapsed", an explicit
    // user choice in localStorage wins.
    mockMatchMedia(false)
    const { result } = renderHook(() => useRightSidebarLayout(), { wrapper: wrap })
    expect(result.current.collapsed).toBe(false)
  })

  it('hydrates collapsed=true from localStorage on first render', () => {
    localStorage.setItem(STORAGE_KEY, 'true')
    // And vice versa — the lg+ viewport default does not override an
    // explicit "collapsed" persisted preference.
    mockMatchMedia(true)
    const { result } = renderHook(() => useRightSidebarLayout(), { wrapper: wrap })
    expect(result.current.collapsed).toBe(true)
  })

  it('toggleCollapsed flips the boolean and persists it to localStorage', () => {
    // Sub-lg viewport so the initial default is collapsed=true and the
    // toggle assertions stay ordered the way the test reads.
    mockMatchMedia(false)
    const { result } = renderHook(() => useRightSidebarLayout(), { wrapper: wrap })
    expect(result.current.collapsed).toBe(true)

    act(() => { result.current.toggleCollapsed() })
    expect(result.current.collapsed).toBe(false)
    expect(localStorage.getItem(STORAGE_KEY)).toBe('false')

    act(() => { result.current.toggleCollapsed() })
    expect(result.current.collapsed).toBe(true)
    expect(localStorage.getItem(STORAGE_KEY)).toBe('true')
  })

  it('setCollapsed writes the exact value', () => {
    const { result } = renderHook(() => useRightSidebarLayout(), { wrapper: wrap })
    act(() => { result.current.setCollapsed(false) })
    expect(result.current.collapsed).toBe(false)
    expect(localStorage.getItem(STORAGE_KEY)).toBe('false')

    act(() => { result.current.setCollapsed(true) })
    expect(result.current.collapsed).toBe(true)
    expect(localStorage.getItem(STORAGE_KEY)).toBe('true')
  })
})

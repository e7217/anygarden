// @vitest-environment jsdom
// Unit tests for the useRightSidebarLayout hook and
// RightSidebarLayoutProvider — a 1:1 mirror of useSidebarLayout (#117)
// for the right-side context rail (#302). Same hydrate/persist/throw
// contract; only the storage key and the default-policy differ.
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import {
  RIGHT_RAIL_WIDTH,
  RightSidebarLayoutProvider,
  clampRailWidth,
  useRightSidebarLayout,
} from './useRightSidebarLayout'

const STORAGE_KEY = 'anygarden_right_sidebar_collapsed'
const WIDTH_KEY = 'anygarden_right_sidebar_width'

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

// #760 — user-adjustable rail width, shared with the thread panel.
describe('useRightSidebarLayout width', () => {
  it('defaults to 320px when nothing is stored', () => {
    const { result } = renderHook(() => useRightSidebarLayout(), { wrapper: wrap })
    expect(result.current.width).toBe(RIGHT_RAIL_WIDTH.default)
    expect(RIGHT_RAIL_WIDTH.default).toBe(320)
  })

  it('hydrates a stored width', () => {
    localStorage.setItem(WIDTH_KEY, '400')
    const { result } = renderHook(() => useRightSidebarLayout(), { wrapper: wrap })
    expect(result.current.width).toBe(400)
  })

  it('clamps an out-of-range stored width', () => {
    localStorage.setItem(WIDTH_KEY, '9999')
    const { result } = renderHook(() => useRightSidebarLayout(), { wrapper: wrap })
    expect(result.current.width).toBe(RIGHT_RAIL_WIDTH.max)
  })

  it('ignores a malformed stored width', () => {
    localStorage.setItem(WIDTH_KEY, 'abc')
    const { result } = renderHook(() => useRightSidebarLayout(), { wrapper: wrap })
    expect(result.current.width).toBe(RIGHT_RAIL_WIDTH.default)
  })

  it('setWidth clamps, rounds and persists', () => {
    const { result } = renderHook(() => useRightSidebarLayout(), { wrapper: wrap })
    act(() => { result.current.setWidth(401.6) })
    expect(result.current.width).toBe(402)
    expect(localStorage.getItem(WIDTH_KEY)).toBe('402')

    act(() => { result.current.setWidth(10) })
    expect(result.current.width).toBe(RIGHT_RAIL_WIDTH.min)
  })

  it('resetWidth returns to the default', () => {
    localStorage.setItem(WIDTH_KEY, '480')
    const { result } = renderHook(() => useRightSidebarLayout(), { wrapper: wrap })
    act(() => { result.current.resetWidth() })
    expect(result.current.width).toBe(RIGHT_RAIL_WIDTH.default)
    expect(localStorage.getItem(WIDTH_KEY)).toBe(String(RIGHT_RAIL_WIDTH.default))
  })

  it('survives a throwing localStorage', () => {
    const spy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied')
    })
    try {
      const { result } = renderHook(() => useRightSidebarLayout(), { wrapper: wrap })
      expect(result.current.width).toBe(RIGHT_RAIL_WIDTH.default)
    } finally {
      spy.mockRestore()
    }
  })

  it('clampRailWidth bounds and rounds', () => {
    expect(clampRailWidth(0)).toBe(RIGHT_RAIL_WIDTH.min)
    expect(clampRailWidth(10_000)).toBe(RIGHT_RAIL_WIDTH.max)
    expect(clampRailWidth(333.4)).toBe(333)
  })
})

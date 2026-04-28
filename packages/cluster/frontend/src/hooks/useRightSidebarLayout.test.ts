// @vitest-environment jsdom
// Unit tests for the useRightSidebarLayout hook and
// RightSidebarLayoutProvider — a 1:1 mirror of useSidebarLayout (#117)
// for the right-side context rail (#302). Same hydrate/persist/throw
// contract; only the storage key differs.
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

beforeEach(() => {
  localStorage.clear()
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

  it('defaults collapsed=true when localStorage has no value', () => {
    // Right rail differs from the left sidebar: default *collapsed*.
    // The room conversation should win the available width by default
    // and the user opts in to the context rail.
    const { result } = renderHook(() => useRightSidebarLayout(), { wrapper: wrap })
    expect(result.current.collapsed).toBe(true)
  })

  it('hydrates collapsed=false from localStorage on first render', () => {
    localStorage.setItem(STORAGE_KEY, 'false')
    const { result } = renderHook(() => useRightSidebarLayout(), { wrapper: wrap })
    expect(result.current.collapsed).toBe(false)
  })

  it('hydrates collapsed=true from localStorage on first render', () => {
    localStorage.setItem(STORAGE_KEY, 'true')
    const { result } = renderHook(() => useRightSidebarLayout(), { wrapper: wrap })
    expect(result.current.collapsed).toBe(true)
  })

  it('toggleCollapsed flips the boolean and persists it to localStorage', () => {
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

// @vitest-environment jsdom
// The overview page's side panel (#1042) keeps its own collapsed flag and
// width, apart from the chat rail's.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { RIGHT_RAIL_WIDTH } from './useRightSidebarLayout'
import { useOverviewPanelLayout } from './useOverviewPanelLayout'

const COLLAPSED_KEY = 'anygarden_overview_panel_collapsed'
const WIDTH_KEY = 'anygarden_overview_panel_width'

beforeEach(() => localStorage.clear())
afterEach(() => {
  localStorage.clear()
  vi.restoreAllMocks()
})

describe('useOverviewPanelLayout', () => {
  it('starts open at the default width', () => {
    const { result } = renderHook(() => useOverviewPanelLayout())
    expect(result.current.collapsed).toBe(false)
    expect(result.current.width).toBe(RIGHT_RAIL_WIDTH.default)
  })

  it('persists the collapsed flag and width, and restores them', () => {
    const first = renderHook(() => useOverviewPanelLayout())
    act(() => first.result.current.toggleCollapsed())
    act(() => first.result.current.setWidth(480))
    expect(localStorage.getItem(COLLAPSED_KEY)).toBe('true')
    expect(localStorage.getItem(WIDTH_KEY)).toBe('480')
    first.unmount()

    const second = renderHook(() => useOverviewPanelLayout())
    expect(second.result.current.collapsed).toBe(true)
    expect(second.result.current.width).toBe(480)
  })

  it('clamps widths and resets to the default', () => {
    localStorage.setItem(WIDTH_KEY, '5000')
    const { result } = renderHook(() => useOverviewPanelLayout())
    expect(result.current.width).toBe(RIGHT_RAIL_WIDTH.max)
    act(() => result.current.setWidth(10))
    expect(result.current.width).toBe(RIGHT_RAIL_WIDTH.min)
    act(() => result.current.resetWidth())
    expect(result.current.width).toBe(RIGHT_RAIL_WIDTH.default)
  })

  it('does not share state with the chat rail', () => {
    localStorage.setItem('anygarden_right_sidebar_collapsed', 'true')
    localStorage.setItem('anygarden_right_sidebar_width', '640')
    const { result } = renderHook(() => useOverviewPanelLayout())
    expect(result.current.collapsed).toBe(false)
    expect(result.current.width).toBe(RIGHT_RAIL_WIDTH.default)
  })

  it('falls back to defaults when storage throws', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked') })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
    const { result } = renderHook(() => useOverviewPanelLayout())
    expect(result.current.collapsed).toBe(false)
    act(() => result.current.setCollapsed(true))
    expect(result.current.collapsed).toBe(true)
  })
})

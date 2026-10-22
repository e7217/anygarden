// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { SIDEBAR_SPLIT, clampSplitRatio, useSidebarSplit } from './useSidebarSplit'

const RATIO_KEY = 'anygarden_sidebar_split_ratio'
const PROJECTS_KEY = 'anygarden_sidebar_projects_collapsed'
const AGENTS_KEY = 'anygarden_sidebar_agents_collapsed'

beforeEach(() => localStorage.clear())
afterEach(() => {
  localStorage.clear()
  vi.restoreAllMocks()
})

describe('clampSplitRatio', () => {
  it('keeps the ratio inside the bounds and rounds it', () => {
    expect(clampSplitRatio(0)).toBe(SIDEBAR_SPLIT.min)
    expect(clampSplitRatio(1)).toBe(SIDEBAR_SPLIT.max)
    expect(clampSplitRatio(0.33333)).toBe(0.333)
    expect(clampSplitRatio(Number.NaN)).toBe(SIDEBAR_SPLIT.default)
  })
})

describe('useSidebarSplit (#928)', () => {
  it('[ROOM-12] starts at an even split with both sections open', () => {
    const { result } = renderHook(() => useSidebarSplit())
    expect(result.current.ratio).toBe(0.5)
    expect(result.current.projectsCollapsed).toBe(false)
    expect(result.current.agentsCollapsed).toBe(false)
  })

  it('[ROOM-12] restores and persists the ratio and collapse flags', () => {
    localStorage.setItem(RATIO_KEY, '0.3')
    localStorage.setItem(AGENTS_KEY, 'true')
    const { result } = renderHook(() => useSidebarSplit())
    expect(result.current.ratio).toBe(0.3)
    expect(result.current.agentsCollapsed).toBe(true)

    act(() => result.current.setRatio(0.7))
    act(() => result.current.toggleProjects())
    act(() => result.current.toggleAgents())
    expect(localStorage.getItem(RATIO_KEY)).toBe('0.7')
    expect(localStorage.getItem(PROJECTS_KEY)).toBe('true')
    expect(localStorage.getItem(AGENTS_KEY)).toBe('false')

    act(() => result.current.resetRatio())
    expect(result.current.ratio).toBe(SIDEBAR_SPLIT.default)
  })

  it('clamps a stored ratio and ignores garbage', () => {
    localStorage.setItem(RATIO_KEY, '5')
    expect(renderHook(() => useSidebarSplit()).result.current.ratio).toBe(SIDEBAR_SPLIT.max)
    localStorage.setItem(RATIO_KEY, 'wide')
    expect(renderHook(() => useSidebarSplit()).result.current.ratio).toBe(SIDEBAR_SPLIT.default)
  })

  it('falls back to defaults when storage throws', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('denied') })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('denied') })
    const { result } = renderHook(() => useSidebarSplit())
    expect(result.current.ratio).toBe(SIDEBAR_SPLIT.default)
    act(() => result.current.toggleAgents())
    expect(result.current.agentsCollapsed).toBe(true)
  })
})

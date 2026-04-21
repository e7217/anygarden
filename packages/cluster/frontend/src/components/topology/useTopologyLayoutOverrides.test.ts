// @vitest-environment jsdom
// Unit tests for useTopologyLayoutOverrides. Covers hydration,
// per-node persistence, scope re-hydration, reset, and the
// null-userId / null-scope no-op path (pre-login + loading states).
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { useTopologyLayoutOverrides } from './useTopologyLayoutOverrides'

const KEY_U1_GLOBAL = 'doorae_topology_layout_v1_u1_global'
const KEY_U1_PERSONAL = 'doorae_topology_layout_v1_u1_personal'

beforeEach(() => {
  localStorage.clear()
})

afterEach(() => {
  localStorage.clear()
})

describe('useTopologyLayoutOverrides', () => {
  it('returns empty overrides when no stored value', () => {
    const { result } = renderHook(() => useTopologyLayoutOverrides('u1', 'global'))
    expect(result.current.overrides).toEqual({})
    expect(result.current.hasOverrides).toBe(false)
  })

  it('hydrates from localStorage on first render', () => {
    localStorage.setItem(KEY_U1_GLOBAL, JSON.stringify({ n1: { x: 10, y: 20 } }))
    const { result } = renderHook(() => useTopologyLayoutOverrides('u1', 'global'))
    expect(result.current.overrides).toEqual({ n1: { x: 10, y: 20 } })
    expect(result.current.hasOverrides).toBe(true)
  })

  it('setPosition persists the position to state and localStorage', () => {
    const { result } = renderHook(() => useTopologyLayoutOverrides('u1', 'global'))
    act(() => {
      result.current.setPosition('n1', { x: 5, y: 6 })
    })
    expect(result.current.overrides).toEqual({ n1: { x: 5, y: 6 } })
    expect(JSON.parse(localStorage.getItem(KEY_U1_GLOBAL) ?? '{}')).toEqual({
      n1: { x: 5, y: 6 },
    })
  })

  it('setPosition merges with existing entries without clobbering', () => {
    localStorage.setItem(KEY_U1_GLOBAL, JSON.stringify({ n1: { x: 1, y: 2 } }))
    const { result } = renderHook(() => useTopologyLayoutOverrides('u1', 'global'))
    act(() => {
      result.current.setPosition('n2', { x: 3, y: 4 })
    })
    expect(result.current.overrides).toEqual({
      n1: { x: 1, y: 2 },
      n2: { x: 3, y: 4 },
    })
  })

  it('reset clears both state and localStorage', () => {
    localStorage.setItem(KEY_U1_GLOBAL, JSON.stringify({ n1: { x: 5, y: 6 } }))
    const { result } = renderHook(() => useTopologyLayoutOverrides('u1', 'global'))
    expect(result.current.hasOverrides).toBe(true)
    act(() => {
      result.current.reset()
    })
    expect(result.current.overrides).toEqual({})
    expect(result.current.hasOverrides).toBe(false)
    expect(localStorage.getItem(KEY_U1_GLOBAL)).toBeNull()
  })

  it('scope switch re-hydrates from the matching key', () => {
    localStorage.setItem(KEY_U1_GLOBAL, JSON.stringify({ n1: { x: 1, y: 1 } }))
    localStorage.setItem(KEY_U1_PERSONAL, JSON.stringify({ n2: { x: 2, y: 2 } }))
    const { result, rerender } = renderHook(
      ({ scope }: { scope: 'global' | 'personal' }) =>
        useTopologyLayoutOverrides('u1', scope),
      { initialProps: { scope: 'global' as 'global' | 'personal' } },
    )
    expect(result.current.overrides).toEqual({ n1: { x: 1, y: 1 } })
    rerender({ scope: 'personal' })
    expect(result.current.overrides).toEqual({ n2: { x: 2, y: 2 } })
  })

  it('is a no-op when userId is null (pre-login)', () => {
    const { result } = renderHook(() => useTopologyLayoutOverrides(null, 'global'))
    act(() => {
      result.current.setPosition('n1', { x: 5, y: 6 })
    })
    expect(result.current.overrides).toEqual({})
    expect(localStorage.length).toBe(0)
  })

  it('is a no-op when scope is null (still loading)', () => {
    const { result } = renderHook(() => useTopologyLayoutOverrides('u1', null))
    act(() => {
      result.current.setPosition('n1', { x: 5, y: 6 })
    })
    expect(result.current.overrides).toEqual({})
    expect(localStorage.length).toBe(0)
  })
})

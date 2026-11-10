// @vitest-environment jsdom
import { act, renderHook } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { useAgentLinkView } from './useAgentLinkView'
afterEach(() => localStorage.clear())
it('restores a user’s positions and viewport without mixing users', () => {
  const { result, rerender, unmount } = renderHook(({ user }) => useAgentLinkView(user), { initialProps: { user: 'u1' } })
  act(() => { result.current.savePosition('a', { x: 8, y: 16 }); result.current.saveViewport({ x: 4, y: 5, zoom: 0.8 }) })
  rerender({ user: 'u2' })
  expect(result.current.view.positions).toEqual({})
  rerender({ user: 'u1' })
  expect(result.current.view.viewport).toEqual({ x: 4, y: 5, zoom: 0.8 })
  unmount()
  const restored = renderHook(() => useAgentLinkView('u1'))
  expect(restored.result.current.view.positions.a).toEqual({ x: 8, y: 16 })
})
it('discards malformed coordinates and out-of-range zoom', () => {
  localStorage.setItem('anygarden_agent_links_view_v1_u', JSON.stringify({ positions: { a: { x: null, y: 2 }, b: { x: 1, y: 2 } }, viewport: { x: 0, y: 0, zoom: 100 } }))
  const { result } = renderHook(() => useAgentLinkView('u'))
  expect(result.current.view.positions).toEqual({ b: { x: 1, y: 2 } })
  expect(result.current.view.viewport).toBeUndefined()
})

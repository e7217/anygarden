// @vitest-environment jsdom
// Behavior tests for useRightRailNotice (#302). The dot only appears
// while the rail is closed — open users see the change live and the
// dot would just be noise on the toggle button.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { act, cleanup, renderHook } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { RightSidebarLayoutProvider, useRightSidebarLayout } from './useRightSidebarLayout'
import { useRightRailNotice } from './useRightRailNotice'

const ROOM = 'room-1'
const STORAGE_KEY = 'anygarden_right_sidebar_collapsed'

function wrap({ children }: { children: ReactNode }) {
  return createElement(RightSidebarLayoutProvider, null, children)
}

beforeEach(() => {
  localStorage.clear()
  vi.restoreAllMocks()
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

function fireTaskEvent(roomId: string) {
  window.dispatchEvent(
    new CustomEvent('anygarden:task:updated', {
      detail: { task: { room_id: roomId } },
    }),
  )
}

describe('useRightRailNotice', () => {
  it('flips to true when a task event arrives while collapsed', () => {
    // Default: collapsed=true (rail closed).
    const { result } = renderHook(() => useRightRailNotice(ROOM), { wrapper: wrap })
    expect(result.current).toBe(false)

    act(() => fireTaskEvent(ROOM))
    expect(result.current).toBe(true)
  })

  it('does not flip when the rail is open', () => {
    localStorage.setItem(STORAGE_KEY, 'false') // start expanded
    const { result } = renderHook(() => useRightRailNotice(ROOM), { wrapper: wrap })
    expect(result.current).toBe(false)

    act(() => fireTaskEvent(ROOM))
    expect(result.current).toBe(false)
  })

  it('resets to false when the rail opens', () => {
    const { result } = renderHook(
      () => {
        const layout = useRightSidebarLayout()
        const notice = useRightRailNotice(ROOM)
        return { layout, notice }
      },
      { wrapper: wrap },
    )

    act(() => fireTaskEvent(ROOM))
    expect(result.current.notice).toBe(true)

    act(() => result.current.layout.setCollapsed(false))
    expect(result.current.notice).toBe(false)
  })

  it('ignores events for other rooms', () => {
    const { result } = renderHook(() => useRightRailNotice(ROOM), { wrapper: wrap })
    act(() => fireTaskEvent('room-2'))
    expect(result.current).toBe(false)
  })
})

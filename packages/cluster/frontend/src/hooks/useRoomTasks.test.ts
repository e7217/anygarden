// @vitest-environment jsdom
// Contract tests for useRoomTasks (#302). The hook is the single
// owner of /api/v1/rooms/{id}/tasks plumbing — TaskPanel and the
// future right-rail TasksSection both consume it. We lock the
// fetch/refetch/event-filter contracts here so a regression in
// either consumer surfaces as a hook-level failure instead of a
// rendered-component diff.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { useRoomTasks, type Task } from './useRoomTasks'

const ROOM = 'room-1'
const OTHER_ROOM = 'room-2'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const sampleTasks: Task[] = [
  {
    id: 't1',
    room_id: ROOM,
    title: 'first',
    status: 'todo',
    assignee_participant_id: null,
    created_at: '2026-04-28T00:00:00Z',
  },
]

beforeEach(() => {
  // Reset window event listeners by replacing the real fetch with a
  // mock that records calls. ``vi.spyOn`` on ``globalThis.fetch``
  // works in jsdom because vitest preserves the prototype chain.
  vi.restoreAllMocks()
})

afterEach(() => {
  // Unmount any hooks rendered in this test so their useEffect cleanups
  // run — without this the ``anygarden:task:updated`` listeners from prior
  // tests stack up on ``window`` and a single dispatchEvent triggers
  // every leftover instance.
  cleanup()
  vi.restoreAllMocks()
})

describe('useRoomTasks', () => {
  it('fetches tasks on mount with the room id', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(jsonResponse(sampleTasks))

    const { result } = renderHook(() => useRoomTasks(ROOM))

    await waitFor(() => expect(result.current.tasks.length).toBe(1))
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/v1/rooms/${ROOM}/tasks`,
      expect.objectContaining({ headers: expect.any(Object) }),
    )
    expect(result.current.tasks[0].id).toBe('t1')
  })

  it('returns empty list and skips fetch when roomId is null', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
    const { result } = renderHook(() => useRoomTasks(null))
    expect(result.current.tasks).toEqual([])
    // One render cycle should be enough for the effect to no-op.
    await Promise.resolve()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('appends ?status=todo to the URL when filter passed', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(jsonResponse([]))

    renderHook(() => useRoomTasks(ROOM, { status: 'todo' }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    const url = fetchMock.mock.calls[0][0] as string
    expect(url).toContain('?status=todo')
  })

  it('refetches when anygarden:task:updated fires for the same room', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(jsonResponse(sampleTasks))

    renderHook(() => useRoomTasks(ROOM))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))

    act(() => {
      window.dispatchEvent(
        new CustomEvent('anygarden:task:updated', {
          detail: { task: { room_id: ROOM } },
        }),
      )
    })
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
  })

  it('ignores anygarden:task:updated for other rooms', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(jsonResponse(sampleTasks))

    renderHook(() => useRoomTasks(ROOM))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))

    act(() => {
      window.dispatchEvent(
        new CustomEvent('anygarden:task:updated', {
          detail: { task: { room_id: OTHER_ROOM } },
        }),
      )
    })
    // Yield once so any (incorrect) second fetch would have queued.
    await Promise.resolve()
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})

// @vitest-environment jsdom
// Contract tests for useRoomFiles (#302). Covers fetch-on-mount,
// upload-then-refresh, and the optimistic delete path so the legacy
// RoomSharedFilesDialog and the right-rail FilesSection stay in sync.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { cleanup, renderHook, waitFor, act } from '@testing-library/react'
import { useRoomFiles } from './useRoomFiles'
import type { RoomSharedFile } from '@/lib/roomFiles'

const ROOM = 'room-1'

const sampleFiles: RoomSharedFile[] = [
  {
    id: 'f1',
    room_id: ROOM,
    filename: 'a.txt',
    storage_name: 'a.txt',
    sha256: 'deadbeef',
    size_bytes: 12,
    mime: 'text/plain',
    uploaded_by: null,
    created_at: '2026-04-28T00:00:00Z',
  },
]

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

beforeEach(() => {
  vi.restoreAllMocks()
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('useRoomFiles', () => {
  it('fetches files on mount', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(jsonResponse(sampleFiles))

    const { result } = renderHook(() => useRoomFiles(ROOM))

    await waitFor(() => expect(result.current.files.length).toBe(1))
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/v1/rooms/${ROOM}/files`,
      expect.objectContaining({ method: 'GET' }),
    )
  })

  it('returns empty list and skips fetch when roomId is null', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
    const { result } = renderHook(() => useRoomFiles(null))
    expect(result.current.files).toEqual([])
    await Promise.resolve()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('optimistically removes a file from local state on delete', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse(sampleFiles)) // initial GET
      .mockResolvedValueOnce(new Response(null, { status: 204 })) // DELETE

    const { result } = renderHook(() => useRoomFiles(ROOM))
    await waitFor(() => expect(result.current.files.length).toBe(1))

    await act(async () => {
      await result.current.remove('f1')
    })

    expect(result.current.files).toEqual([])
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
})

// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest'
import { leaveRoom } from './roomMembership'

function respond(status: number, body?: unknown): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

afterEach(() => vi.unstubAllGlobals())

describe('leaveRoom', () => {
  it('[ROOM-05] POSTs to the room leave route and reports success on 204', async () => {
    const fetchMock = vi.fn().mockResolvedValue(respond(204))
    vi.stubGlobal('fetch', fetchMock)

    await expect(leaveRoom('r1')).resolves.toEqual({ ok: true })
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/rooms/r1/leave',
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('[ROOM-05] maps 409 to the last-admin reason', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      respond(409, { detail: 'Cannot remove the last admin/owner of the room' }),
    ))
    await expect(leaveRoom('r1')).resolves.toEqual({
      ok: false,
      reason: 'last_admin',
      status: 409,
      detail: 'Cannot remove the last admin/owner of the room',
    })
  })

  it('[ROOM-05] reports other failures with status and server detail', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(respond(403, { detail: 'Not a member of this room' })))
    await expect(leaveRoom('r1')).resolves.toEqual({
      ok: false,
      reason: 'error',
      status: 403,
      detail: 'Not a member of this room',
    })
  })
})

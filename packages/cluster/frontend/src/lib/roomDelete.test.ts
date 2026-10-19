import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiFetch } from '@/lib/api'
import { deleteRoom } from './roomDelete'

vi.mock('@/lib/api', () => ({ apiFetch: vi.fn() }))
afterEach(() => vi.resetAllMocks())

describe('deleteRoom (ROOM-11)', () => {
  it('[ROOM-11] treats 204 as success', async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce(new Response(null, { status: 204 }))
    expect(await deleteRoom('r1')).toEqual({ ok: true })
    expect(vi.mocked(apiFetch).mock.calls[0]).toEqual(['/api/v1/rooms/r1', { method: 'DELETE' }])
  })

  it('[ROOM-11] recognises a run the caller cannot stop', async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce(new Response(JSON.stringify({
      detail: { code: 'ROOM_HAS_ACTIVE_EXECUTION', detail: 'stop it first' },
    }), { status: 409 }))
    expect(await deleteRoom('r1')).toEqual({
      ok: false, reason: 'active_execution', status: 409, detail: 'stop it first',
    })
  })

  it('keeps a plain string detail for other failures', async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'Forbidden' }), { status: 403 }))
    expect(await deleteRoom('r1')).toEqual({ ok: false, reason: 'error', status: 403, detail: 'Forbidden' })
  })
})

describe('deleteRoomFailure', () => {
  it('[ROOM-11] prefers the active-run guidance, then server detail, then the fallback', async () => {
    const { deleteRoomFailure } = await import('./roomDelete')
    const base = { ok: false as const, status: 409 }
    expect(deleteRoomFailure({ ...base, reason: 'active_execution', detail: 'x' }, 'fb', 'run')).toBe('run')
    expect(deleteRoomFailure({ ...base, reason: 'error', detail: 'server' }, 'fb', 'run')).toBe('server')
    expect(deleteRoomFailure({ ...base, reason: 'error', detail: null }, 'fb', 'run')).toBe('fb')
  })
})

import { apiFetch } from './api'

/** Result of ``POST /api/v1/rooms/{id}/leave`` (ROOM-05). */
export type LeaveRoomResult =
  | { ok: true }
  | {
      ok: false
      /** ``last_admin`` — the server refused because the caller is the
       *  room's only admin/owner (409). DM rooms also answer 409, but the
       *  UI never offers leave on a DM. */
      reason: 'last_admin' | 'error'
      status: number
      detail: string | null
    }

export async function leaveRoom(roomId: string): Promise<LeaveRoomResult> {
  const resp = await apiFetch(`/api/v1/rooms/${roomId}/leave`, { method: 'POST' })
  if (resp.status === 204 || resp.ok) return { ok: true }
  let detail: string | null = null
  try {
    const body = await resp.json()
    if (body && typeof body.detail === 'string') detail = body.detail
  } catch { /* empty or non-JSON body */ }
  return {
    ok: false,
    reason: resp.status === 409 ? 'last_admin' : 'error',
    status: resp.status,
    detail,
  }
}

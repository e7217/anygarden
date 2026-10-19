import { apiFetch } from '@/lib/api'

/** Result of ``DELETE /api/v1/rooms/{id}`` (ROOM-11). The server keeps the
 *  room's history and hides it from lists; open agent work is cancelled. */
export type DeleteRoomResult =
  | { ok: true }
  | {
      ok: false
      /** ``active_execution`` — a project run the caller may not stop is
       *  still working in the room (409 ``ROOM_HAS_ACTIVE_EXECUTION``). */
      reason: 'active_execution' | 'error'
      status: number
      detail: string | null
    }

export async function deleteRoom(roomId: string): Promise<DeleteRoomResult> {
  const resp = await apiFetch(`/api/v1/rooms/${roomId}`, { method: 'DELETE' })
  if (resp.status === 204 || resp.ok) return { ok: true }
  let detail: string | null = null
  let code: unknown = null
  try {
    const body = await resp.json()
    if (body && typeof body.detail === 'string') detail = body.detail
    else if (body?.detail && typeof body.detail === 'object') {
      code = body.detail.code
      if (typeof body.detail.detail === 'string') detail = body.detail.detail
    }
  } catch { /* empty or non-JSON body */ }
  return {
    ok: false,
    reason: code === 'ROOM_HAS_ACTIVE_EXECUTION' ? 'active_execution' : 'error',
    status: resp.status,
    detail,
  }
}

/** The message to show for a failed delete: the active-run guidance, else the
 *  server's text, else *fallback*. */
export function deleteRoomFailure(
  result: Extract<DeleteRoomResult, { ok: false }>,
  fallback: string,
  activeExecutionMessage: string,
): string {
  if (result.reason === 'active_execution') return activeExecutionMessage
  return result.detail ?? fallback
}

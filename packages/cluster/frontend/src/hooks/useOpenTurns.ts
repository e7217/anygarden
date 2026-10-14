import { useCallback, useEffect, useState } from 'react'
import { apiFetch } from '@/lib/api'
import type { OpenTurn } from '@/lib/openTurns'

/**
 * ENG-09 — the room's open general turns, refreshed whenever the set of
 * working participants changes (a turn starting or ending flips typing).
 *
 * ``roomId`` null disables the hook (guests, whom the endpoints reject).
 */
export function useOpenTurns(roomId: string | null, workingIds: Iterable<string>) {
  const [turns, setTurns] = useState<OpenTurn[]>([])
  const workingKey = Array.from(workingIds).sort().join(',')

  useEffect(() => {
    setTurns([])
    if (!roomId || !workingKey) return
    let active = true
    apiFetch(`/api/v1/turns/open?room_id=${encodeURIComponent(roomId)}`, {})
      .then(resp => (resp.ok ? resp.json() : []))
      .then((rows: OpenTurn[]) => {
        if (active && Array.isArray(rows)) setTurns(rows)
      })
      .catch(() => {})
    return () => {
      active = false
    }
  }, [roomId, workingKey])

  /** Cancels every given turn; resolves true when all of them stopped. */
  const cancel = useCallback(async (requestIds: string[]): Promise<boolean> => {
    const results = await Promise.all(requestIds.map(async id => {
      try {
        const resp = await apiFetch(`/api/v1/turns/${encodeURIComponent(id)}/cancel`, { method: 'POST' })
        return resp.ok ? id : null
      } catch {
        return null
      }
    }))
    const stopped = new Set(results.filter((id): id is string => id !== null))
    setTurns(previous => previous.filter(turn => !stopped.has(turn.request_id)))
    return stopped.size === requestIds.length
  }, [])

  return { turns, cancel }
}

import { useCallback, useEffect, useRef, useState } from 'react'
import { apiFetch } from '@/lib/api'
import type { Room } from '@/hooks/useRooms'

/** Uses the existing room authorization/filter, including for non-admins. */
export function useAgentConversations(agentId: string, userId: string | null) {
  const key = `${userId ?? ''}:${agentId}`
  const [state, setState] = useState<{ key: string; rooms: Room[]; loading: boolean; errorStatus: number | null }>({ key, rooms: [], loading: true, errorStatus: null })
  const controller = useRef<AbortController | null>(null)
  const refresh = useCallback(async () => {
    controller.current?.abort()
    if (!userId) return
    const request = new AbortController()
    controller.current = request
    setState({ key, rooms: [], loading: true, errorStatus: null })
    try {
      const response = await apiFetch(`/api/v1/rooms?is_dm=true&representative_agent_id=${encodeURIComponent(agentId)}`, { signal: request.signal })
      if (!response.ok) throw { status: response.status }
      const rooms: Room[] = await response.json()
      if (!Array.isArray(rooms)) throw new Error('Invalid conversation list')
      if (!request.signal.aborted) setState({ key, rooms: [...rooms].sort((a, b) => a.name.localeCompare(b.name)), loading: false, errorStatus: null })
    } catch (error) {
      if (!request.signal.aborted) setState({ key, rooms: [], loading: false, errorStatus: (error as { status?: number }).status ?? 0 })
    }
  }, [key, userId, agentId])
  useEffect(() => { void refresh(); return () => controller.current?.abort() }, [refresh])
  // Never expose the previous selection/user for the render before its effect.
  const current = state.key === key && userId ? state : { rooms: [], loading: !!userId, errorStatus: null }
  return { ...current, refresh }
}

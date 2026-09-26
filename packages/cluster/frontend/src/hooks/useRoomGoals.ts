import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  deleteGoal,
  listRoomGoals,
  pauseGoal,
  resumeGoal,
  runGoalNow,
  type Goal,
} from '@/lib/goals'

export interface UseRoomGoalsValue {
  goals: Goal[]
  loading: boolean
  error: string | null
  refresh: () => Promise<void>
  remove: (goalId: string) => Promise<void>
  runNow: (goalId: string) => Promise<void>
  pause: (goalId: string) => Promise<void>
  resume: (goalId: string) => Promise<void>
}

/**
 * Subscribe to all goals whose ``report_room_id`` is the active
 * room (#302). The right-rail GoalsSection consumes this; passing
 * ``null`` suspends fetching.
 */
export function useRoomGoals(roomId: string | null): UseRoomGoalsValue {
  const scope = useMemo(() => ({ roomId, active: true, request: 0 }), [roomId])
  const currentScope = useRef(scope)
  currentScope.current = scope
  const [snapshot, setSnapshot] = useState<{ scope: typeof scope; goals: Goal[]; loading: boolean; error: string | null }>(
    () => ({ scope, goals: [], loading: Boolean(roomId), error: null }),
  )
  const isCurrent = useCallback(() => scope.active && currentScope.current === scope, [scope])

  const refresh = useCallback(async () => {
    if (!roomId || !isCurrent()) return
    const request = ++scope.request
    const accepts = () => isCurrent() && scope.request === request
    setSnapshot(previous => ({ scope, goals: previous.scope === scope ? previous.goals : [], loading: true, error: null }))
    try {
      const goals = await listRoomGoals(roomId)
      if (accepts()) setSnapshot({ scope, goals, loading: false, error: null })
    } catch (error) {
      if (accepts()) setSnapshot({ scope, goals: [], loading: false, error: error instanceof Error ? error.message : String(error) })
    }
  }, [roomId, scope, isCurrent])

  useEffect(() => {
    scope.active = true
    void refresh()
    return () => { scope.active = false }
  }, [scope, refresh])

  useEffect(() => {
    if (!roomId) return
    const handler = () => { void refresh() }
    window.addEventListener('anygarden:goal:updated', handler)
    return () => window.removeEventListener('anygarden:goal:updated', handler)
  }, [roomId, refresh])

  const mutate = useCallback(async (action: () => Promise<unknown>) => {
    if (!roomId || !isCurrent()) return
    try {
      await action()
      if (isCurrent()) await refresh()
    } catch (error) {
      // The old view's handler must not notify the user in another room.
      if (isCurrent()) throw error
    }
  }, [roomId, isCurrent, refresh])
  const remove = useCallback((id: string) => mutate(() => deleteGoal(id)), [mutate])
  const runNow = useCallback((id: string) => mutate(() => runGoalNow(id)), [mutate])
  const pause = useCallback((id: string) => mutate(() => pauseGoal(id)), [mutate])
  const resume = useCallback((id: string) => mutate(() => resumeGoal(id)), [mutate])

  const current = snapshot.scope === scope && roomId ? snapshot : { goals: [], loading: Boolean(roomId), error: null }
  return { goals: current.goals, loading: current.loading, error: current.error, refresh, remove, runNow, pause, resume }
}

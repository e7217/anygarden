import { useCallback, useEffect, useState } from 'react'
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
  const [goals, setGoals] = useState<Goal[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    if (!roomId) {
      setGoals([])
      return
    }
    setLoading(true)
    setError(null)
    try {
      setGoals(await listRoomGoals(roomId))
    } catch (e) {
      setGoals([])
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [roomId])

  useEffect(() => {
    refresh()
  }, [refresh])

  // ``doorae:goal:updated`` is the broadcast we'll start firing from
  // the server in a follow-up — listen for it now so a server-side
  // upgrade lights up live without a frontend redeploy.
  useEffect(() => {
    if (!roomId) return
    const handler = () => {
      refresh()
    }
    window.addEventListener('doorae:goal:updated', handler)
    return () => window.removeEventListener('doorae:goal:updated', handler)
  }, [roomId, refresh])

  const remove = useCallback(async (goalId: string) => {
    await deleteGoal(goalId)
    setGoals((prev) => prev.filter((g) => g.id !== goalId))
  }, [])

  const runNow = useCallback(
    async (goalId: string) => {
      await runGoalNow(goalId)
      await refresh()
    },
    [refresh],
  )

  const pause = useCallback(
    async (goalId: string) => {
      const updated = await pauseGoal(goalId)
      setGoals((prev) => prev.map((g) => (g.id === goalId ? updated : g)))
    },
    [],
  )

  const resume = useCallback(
    async (goalId: string) => {
      const updated = await resumeGoal(goalId)
      setGoals((prev) => prev.map((g) => (g.id === goalId ? updated : g)))
    },
    [],
  )

  return { goals, loading, error, refresh, remove, runNow, pause, resume }
}

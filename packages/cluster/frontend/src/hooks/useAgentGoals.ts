import { useCallback, useEffect, useState } from 'react'
import {
  deleteGoal,
  listAgentGoals,
  pauseGoal,
  resumeGoal,
  runGoalNow,
  type Goal,
} from '@/lib/goals'

export interface UseAgentGoalsValue {
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
 * Subscribe to a single agent's goals across all rooms (#302).
 * AgentSettingsDialog's Goals section consumes this; the call is
 * the dual of useRoomGoals (room-scoped vs. agent-scoped).
 */
export function useAgentGoals(agentId: string | null): UseAgentGoalsValue {
  const [goals, setGoals] = useState<Goal[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    if (!agentId) {
      setGoals([])
      return
    }
    setLoading(true)
    setError(null)
    try {
      setGoals(await listAgentGoals(agentId))
    } catch (e) {
      setGoals([])
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [agentId])

  useEffect(() => {
    refresh()
  }, [refresh])

  useEffect(() => {
    if (!agentId) return
    const handler = () => {
      refresh()
    }
    window.addEventListener('doorae:goal:updated', handler)
    return () => window.removeEventListener('doorae:goal:updated', handler)
  }, [agentId, refresh])

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

  const pause = useCallback(async (goalId: string) => {
    const updated = await pauseGoal(goalId)
    setGoals((prev) => prev.map((g) => (g.id === goalId ? updated : g)))
  }, [])

  const resume = useCallback(async (goalId: string) => {
    const updated = await resumeGoal(goalId)
    setGoals((prev) => prev.map((g) => (g.id === goalId ? updated : g)))
  }, [])

  return { goals, loading, error, refresh, remove, runNow, pause, resume }
}

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
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
 * Subscribe to one agent's responsibilities across rooms. A new agent
 * or a newer refresh invalidates previous reads and mutation callbacks.
 */
export function useAgentGoals(agentId: string | null): UseAgentGoalsValue {
  const scope = useMemo(() => ({ agentId, active: true, request: 0 }), [agentId])
  const currentScope = useRef(scope)
  currentScope.current = scope
  const [snapshot, setSnapshot] = useState<{ scope: typeof scope; goals: Goal[]; loading: boolean; error: string | null }>(
    () => ({ scope, goals: [], loading: Boolean(agentId), error: null }),
  )
  const isCurrent = useCallback(() => scope.active && currentScope.current === scope, [scope])

  const refresh = useCallback(async () => {
    if (!agentId || !isCurrent()) return
    const request = ++scope.request
    const accepts = () => isCurrent() && scope.request === request
    setSnapshot(previous => ({ scope, goals: previous.scope === scope ? previous.goals : [], loading: true, error: null }))
    try {
      const goals = await listAgentGoals(agentId)
      if (accepts()) setSnapshot({ scope, goals, loading: false, error: null })
    } catch (error) {
      if (accepts()) setSnapshot({ scope, goals: [], loading: false, error: error instanceof Error ? error.message : String(error) })
    }
  }, [agentId, scope, isCurrent])

  useEffect(() => {
    scope.active = true
    void refresh()
    return () => { scope.active = false }
  }, [scope, refresh])

  useEffect(() => {
    if (!agentId) return
    const handler = () => { void refresh() }
    window.addEventListener('anygarden:goal:updated', handler)
    return () => window.removeEventListener('anygarden:goal:updated', handler)
  }, [agentId, refresh])

  const mutate = useCallback(async (action: () => Promise<unknown>) => {
    if (!agentId || !isCurrent()) return
    try {
      await action()
      if (isCurrent()) await refresh()
    } catch (error) {
      // The previous agent's handler must not notify the newly selected view.
      if (isCurrent()) throw error
    }
  }, [agentId, isCurrent, refresh])
  const remove = useCallback((id: string) => mutate(() => deleteGoal(id)), [mutate])
  const runNow = useCallback((id: string) => mutate(() => runGoalNow(id)), [mutate])
  const pause = useCallback((id: string) => mutate(() => pauseGoal(id)), [mutate])
  const resume = useCallback((id: string) => mutate(() => resumeGoal(id)), [mutate])

  const current = snapshot.scope === scope && agentId ? snapshot : { goals: [], loading: Boolean(agentId), error: null }
  return { goals: current.goals, loading: current.loading, error: current.error, refresh, remove, runNow, pause, resume }
}

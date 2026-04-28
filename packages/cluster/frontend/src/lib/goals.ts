/**
 * REST helpers + types for the Goal subsystem (#302 Phase 3).
 *
 * Mirrors the server schemas in ``api/v1/goals.py``. Keep the field
 * shape sync'd with ``GoalOut``; new server fields can land as
 * optional here without breaking older clients.
 */
import { apiFetch } from '@/lib/api'

export type GoalStatus =
  | 'active'
  | 'paused'
  | 'completed'
  | 'failed'
  | 'abandoned'

export type GoalTriggerType = 'cron' | 'interval' | 'manual'

export type GoalMaterialize = 'full' | 'interesting_only'

export interface Goal {
  id: string
  assignee_agent_id: string
  owner_id: string
  report_room_id: string | null
  title: string
  spec: string
  status: GoalStatus
  trigger_type: GoalTriggerType
  trigger_config: Record<string, unknown>
  materialize: GoalMaterialize
  consecutive_failures: number
  next_run_at: string | null
  last_run_at: string | null
  created_at: string
  updated_at: string
}

export interface GoalCreateInput {
  title: string
  spec: string
  trigger_type: GoalTriggerType
  trigger_config: Record<string, unknown>
  materialize?: GoalMaterialize
  report_room_id?: string | null
}

export interface GoalUpdateInput {
  title?: string
  spec?: string
  trigger_type?: GoalTriggerType
  trigger_config?: Record<string, unknown>
  materialize?: GoalMaterialize
  report_room_id?: string | null
  status?: GoalStatus
}

async function jsonOrThrow<T>(resp: Response, action: string): Promise<T> {
  if (!resp.ok) {
    let detail = ''
    try {
      const body = await resp.json()
      detail = typeof body?.detail === 'string' ? body.detail : ''
    } catch {
      /* best-effort */
    }
    throw new Error(detail || `${action} failed (HTTP ${resp.status})`)
  }
  return resp.json() as Promise<T>
}

export async function createGoal(
  agentId: string,
  input: GoalCreateInput,
): Promise<Goal> {
  const resp = await apiFetch(`/api/v1/agents/${agentId}/goals`, {
    method: 'POST',
    body: JSON.stringify(input),
  })
  return jsonOrThrow<Goal>(resp, 'Create goal')
}

export async function listAgentGoals(agentId: string): Promise<Goal[]> {
  const resp = await apiFetch(`/api/v1/agents/${agentId}/goals`)
  return jsonOrThrow<Goal[]>(resp, 'List agent goals')
}

export async function listRoomGoals(roomId: string): Promise<Goal[]> {
  const resp = await apiFetch(`/api/v1/rooms/${roomId}/goals`)
  return jsonOrThrow<Goal[]>(resp, 'List room goals')
}

export async function getGoal(goalId: string): Promise<Goal> {
  const resp = await apiFetch(`/api/v1/goals/${goalId}`)
  return jsonOrThrow<Goal>(resp, 'Get goal')
}

export async function updateGoal(
  goalId: string,
  patch: GoalUpdateInput,
): Promise<Goal> {
  const resp = await apiFetch(`/api/v1/goals/${goalId}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  })
  return jsonOrThrow<Goal>(resp, 'Update goal')
}

export async function deleteGoal(goalId: string): Promise<void> {
  const resp = await apiFetch(`/api/v1/goals/${goalId}`, {
    method: 'DELETE',
  })
  if (!resp.ok && resp.status !== 204) {
    throw new Error(`Delete goal failed (HTTP ${resp.status})`)
  }
}

export async function runGoalNow(goalId: string): Promise<Goal> {
  const resp = await apiFetch(`/api/v1/goals/${goalId}/run`, {
    method: 'POST',
  })
  return jsonOrThrow<Goal>(resp, 'Manual run')
}

export async function pauseGoal(goalId: string): Promise<Goal> {
  const resp = await apiFetch(`/api/v1/goals/${goalId}/pause`, {
    method: 'POST',
  })
  return jsonOrThrow<Goal>(resp, 'Pause goal')
}

export async function resumeGoal(goalId: string): Promise<Goal> {
  const resp = await apiFetch(`/api/v1/goals/${goalId}/resume`, {
    method: 'POST',
  })
  return jsonOrThrow<Goal>(resp, 'Resume goal')
}

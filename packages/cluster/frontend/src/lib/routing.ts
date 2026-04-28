/**
 * Client for the auto-route REST endpoint (#313).
 *
 * Mirrors the server's ``AutoRouteResult`` Pydantic shape so the UI
 * can render the per-task outcome inline without an extra fetch.
 * Server 422 / 502 / 504 responses surface the ``detail`` field —
 * the rail's toast uses that string directly.
 */
import { apiFetch } from '@/lib/api'

export interface RoutedTask {
  task_id: string
  assignee_agent_id: string
}

export interface SkippedTask {
  task_id: string
  reason: string
}

export interface AutoRouteResult {
  routed: RoutedTask[]
  skipped: SkippedTask[]
  rep_agent_id: string
  request_id: string
}

export async function autoRouteUnassigned(
  roomId: string,
): Promise<AutoRouteResult> {
  const resp = await apiFetch(
    `/api/v1/rooms/${roomId}/auto-route-unassigned`,
    { method: 'POST' },
  )
  if (!resp.ok) {
    let detail = ''
    try {
      const body = await resp.json()
      detail = typeof body?.detail === 'string' ? body.detail : ''
    } catch {
      /* best-effort */
    }
    throw new Error(detail || `Auto-route failed (HTTP ${resp.status})`)
  }
  return resp.json() as Promise<AutoRouteResult>
}

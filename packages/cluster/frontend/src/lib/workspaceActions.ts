/**
 * Workspace actions an agent asked a person to approve (#938).
 *
 * The agent's message carries ``metadata.workspace_action_request`` and
 * renders as an approval card; the server's report back to the agent
 * carries ``metadata.workspace_action_result``. Mirrors
 * ``anygarden/workspace/router.py``.
 */
import { apiFetch } from '@/lib/api'

export type WorkspaceActionStatus = 'pending' | 'executing' | 'executed' | 'rejected' | 'expired' | 'failed'

export interface WorkspaceAction {
  id: string
  room_id: string
  agent_id: string
  action: string
  summary: string
  /** Everything that runs on approval; shown in full on the card. */
  params: Record<string, unknown>
  status: WorkspaceActionStatus
  result: Record<string, unknown> | null
  error: string | null
  admin_only: boolean
  can_decide: boolean
  expires_at: string
}

export interface WorkspaceActionRequestMeta {
  request_id: string
  action: string
}

export interface WorkspaceActionResultMeta {
  request_id: string
  status: WorkspaceActionStatus
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : null
}

export function parseWorkspaceActionRequest(metadata: unknown): WorkspaceActionRequestMeta | null {
  const meta = record(record(metadata)?.workspace_action_request)
  return meta && typeof meta.request_id === 'string' && typeof meta.action === 'string'
    ? { request_id: meta.request_id, action: meta.action }
    : null
}

export function parseWorkspaceActionResult(metadata: unknown): WorkspaceActionResultMeta | null {
  const meta = record(record(metadata)?.workspace_action_result)
  return meta && typeof meta.request_id === 'string' && typeof meta.status === 'string'
    ? { request_id: meta.request_id, status: meta.status as WorkspaceActionStatus }
    : null
}

async function read(resp: Response): Promise<WorkspaceAction> {
  if (!resp.ok) {
    let message = ''
    try {
      const body = await resp.json()
      message = typeof body?.detail === 'string' ? body.detail : body?.detail?.message ?? ''
    } catch {
      /* best-effort */
    }
    throw new Error(message || `HTTP ${resp.status}`)
  }
  return resp.json() as Promise<WorkspaceAction>
}

export async function getWorkspaceAction(id: string): Promise<WorkspaceAction> {
  return read(await apiFetch(`/api/v1/workspace-actions/${id}`))
}

/** Goals and routines (#947, #948) can be approved with values edited in their form. */
export const EDITABLE_ACTIONS: ReadonlySet<string> = new Set(['create_goal', 'create_routine'])

export async function decideWorkspaceAction(
  id: string, decision: 'approve' | 'reject', params?: Record<string, unknown>,
): Promise<WorkspaceAction> {
  const init: RequestInit = params
    ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ params }) }
    : { method: 'POST' }
  return read(await apiFetch(`/api/v1/workspace-actions/${id}/${decision}`, init))
}

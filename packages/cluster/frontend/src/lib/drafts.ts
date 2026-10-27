/**
 * Goal and routine drafts an agent writes for a person (#947, #948).
 *
 * ``/goal <description>`` and ``/routine [@agent] <description>`` ask an
 * agent for a draft (``POST /rooms/{id}/drafts``). The server's request
 * notice carries ``metadata.draft_request``; the draft arrives as a
 * workspace approval card; a turn that ends without one leaves a notice
 * with ``metadata.draft_request_failed``. Mirrors
 * ``anygarden/workspace/drafts.py``.
 */
import { apiFetch } from '@/lib/api'
import type { Participant } from '@/lib/participants'
import type { Room } from '@/hooks/useRooms'

export type DraftKind = 'goal' | 'routine'

/** Opens the ``/goal`` or ``/routine`` form; ``detail`` is a ``SlashFormRequest``. */
export const SLASH_FORM_OPEN_EVENT = 'anygarden:slash-form:open'

export interface DraftRequestMeta {
  id: string
  kind: DraftKind
  description: string
  drafter_participant_id: string
}

export interface DraftFailedMeta {
  id: string
  kind: DraftKind
  description: string
  drafter_participant_id: string | null
  assignee_agent_id: string | null
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : null
}

const isKind = (value: unknown): value is DraftKind => value === 'goal' || value === 'routine'

export function parseDraftRequest(metadata: unknown): DraftRequestMeta | null {
  const meta = record(record(metadata)?.draft_request)
  if (!meta || typeof meta.id !== 'string' || !isKind(meta.kind)) return null
  return {
    id: meta.id,
    kind: meta.kind,
    description: typeof meta.description === 'string' ? meta.description : '',
    drafter_participant_id: typeof meta.drafter_participant_id === 'string' ? meta.drafter_participant_id : '',
  }
}

export function parseDraftFailed(metadata: unknown): DraftFailedMeta | null {
  const meta = record(record(metadata)?.draft_request_failed)
  if (!meta || typeof meta.id !== 'string' || !isKind(meta.kind)) return null
  return {
    id: meta.id,
    kind: meta.kind,
    description: typeof meta.description === 'string' ? meta.description : '',
    drafter_participant_id: typeof meta.drafter_participant_id === 'string' ? meta.drafter_participant_id : null,
    assignee_agent_id: typeof meta.assignee_agent_id === 'string' ? meta.assignee_agent_id : null,
  }
}

/** Who will draft a routine, decided as the server does. ``null`` means the
 * person must name an agent; no agents at all is the caller's own case. */
export function routineDrafter(
  room: Room | null | undefined,
  participants: Record<string, Participant>,
  assigneePid: string | null,
): Participant | null {
  if (assigneePid) return participants[assigneePid] ?? null
  const agents = Object.values(participants).filter(p => p.agent_id)
  if (agents.length === 1) return agents[0]
  return agents.find(p => p.agent_id === room?.representative_agent_id) ?? null
}

/** The room's lead, who drafts every goal. */
export function goalDrafter(
  room: Room | null | undefined,
  participants: Record<string, Participant>,
): Participant | null {
  if (!room?.representative_agent_id) return null
  return Object.values(participants).find(p => p.agent_id === room.representative_agent_id) ?? null
}

export interface DraftRequestInput {
  kind: DraftKind
  description: string
  agent_id?: string | null
  timezone?: string | null
}

export function browserTimezone(): string | null {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || null
  } catch {
    return null
  }
}

export async function requestDraft(roomId: string, input: DraftRequestInput): Promise<void> {
  const resp = await apiFetch(`/api/v1/rooms/${roomId}/drafts`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
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
}

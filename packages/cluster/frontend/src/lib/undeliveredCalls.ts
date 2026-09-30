// #743 — peer calls the server refused to deliver. The agent's sentence
// that made the call stays in the message; ``metadata.peer_call_undelivered``
// lists who was not called and why.

export type UndeliveredReason = 'already_answering' | 'limit_reached'

export interface UndeliveredCallGroup {
  reason: UndeliveredReason
  participantIds: string[]
}

const REASONS: readonly UndeliveredReason[] = ['already_answering', 'limit_reached']

function isReason(value: unknown): value is UndeliveredReason {
  return typeof value === 'string' && (REASONS as readonly string[]).includes(value)
}

/** Group the undelivered calls by reason, in first-seen order. Malformed
 * entries are skipped so an unexpected payload never breaks the bubble. */
export function extractUndeliveredCalls(
  metadata: Record<string, unknown> | undefined,
): UndeliveredCallGroup[] {
  const raw = metadata?.peer_call_undelivered
  if (!Array.isArray(raw)) return []

  const groups: UndeliveredCallGroup[] = []
  for (const entry of raw) {
    if (!entry || typeof entry !== 'object') continue
    const { participant_id: pid, reason } = entry as Record<string, unknown>
    if (typeof pid !== 'string' || !pid || !isReason(reason)) continue
    let group = groups.find(g => g.reason === reason)
    if (!group) {
      group = { reason, participantIds: [] }
      groups.push(group)
    }
    if (!group.participantIds.includes(pid)) group.participantIds.push(pid)
  }
  return groups
}

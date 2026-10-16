// #854 — files an agent attached to its reply. The server rebuilds every
// ``artifact`` reference from ``room_artifacts``; ``undelivered_attachments``
// names the files the agent wrote but could not attach.

export interface ArtifactReference {
  id: string
  roomId: string
  name: string
  mime: string
  sizeBytes: number
}

export type UndeliveredAttachmentReason =
  | 'too_large'
  | 'unsupported_type'
  | 'too_many'
  | 'upload_failed'

export interface UndeliveredAttachment {
  name: string
  reason: UndeliveredAttachmentReason
}

const REASONS: readonly UndeliveredAttachmentReason[] = [
  'too_large',
  'unsupported_type',
  'too_many',
  'upload_failed',
]

/** Artifact references in message order. Malformed entries are skipped so
 * an unexpected payload never breaks the bubble. */
export function extractArtifactReferences(
  metadata?: Record<string, unknown> | null,
): ArtifactReference[] {
  const raw = metadata?.references
  if (!Array.isArray(raw)) return []
  const refs: ArtifactReference[] = []
  const seen = new Set<string>()
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue
    const ref = item as Record<string, unknown>
    if (ref.type !== 'artifact') continue
    const { id, room_id: roomId, name, mime, size_bytes: sizeBytes } = ref
    if (typeof id !== 'string' || typeof roomId !== 'string' || typeof name !== 'string') continue
    if (seen.has(id)) continue
    seen.add(id)
    refs.push({
      id,
      roomId,
      name,
      mime: typeof mime === 'string' ? mime : 'application/octet-stream',
      sizeBytes: typeof sizeBytes === 'number' ? sizeBytes : 0,
    })
  }
  return refs
}

export function extractUndeliveredAttachments(
  metadata?: Record<string, unknown> | null,
): UndeliveredAttachment[] {
  const raw = metadata?.undelivered_attachments
  if (!Array.isArray(raw)) return []
  const out: UndeliveredAttachment[] = []
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue
    const { name, reason } = item as Record<string, unknown>
    if (typeof name !== 'string' || !name) continue
    out.push({
      name,
      reason: (REASONS as readonly unknown[]).includes(reason)
        ? (reason as UndeliveredAttachmentReason)
        : 'upload_failed',
    })
  }
  return out
}

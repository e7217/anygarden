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

const OUTBOX_PATH = /(?:^|\/)memory\/outbox\/(?:([^/]+)\/)?([^/?#]+)$/

function decode(value: string): string {
  try { return decodeURIComponent(value) } catch { return value }
}

/** #964 — an agent's reply may link a file by its outbox path
 * (``memory/outbox/<room_id>/<name>``), which is no address in the app.
 *
 * Returns ``null`` for any other link. For an outbox link it returns the
 * attached artifact's download address when the message carries a file of
 * that name (preferring the same room), else ``{ href: null }`` so the
 * link is shown as text. */
export function resolveOutboxHref(
  href: string | undefined,
  references: readonly ArtifactReference[],
): { href: string | null } | null {
  if (!href) return null
  const scheme = /^([a-z][a-z0-9+.-]*):/i.exec(href)?.[1]?.toLowerCase()
  if (scheme && scheme !== 'file') return null
  const path = href.split(/[?#]/)[0]
  const match = OUTBOX_PATH.exec(path)
  if (!match) return null
  const roomId = match[1] ? decode(match[1]) : null
  const name = decode(match[2])
  const named = references.filter(ref => ref.name === name)
  const ref = named.find(r => roomId !== null && r.roomId === roomId) ?? named[0]
  if (!ref) return { href: null }
  return {
    href: `/api/v1/rooms/${encodeURIComponent(ref.roomId)}/artifacts/${encodeURIComponent(ref.id)}`,
  }
}

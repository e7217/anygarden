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

/** #1017 — a link in the reply body and the file the agent attached for it. */
export interface LinkedFile {
  href: string
  artifactId: string
}

export function extractLinkedFiles(
  metadata?: Record<string, unknown> | null,
): LinkedFile[] {
  const raw = metadata?.linked_files
  if (!Array.isArray(raw)) return []
  const out: LinkedFile[] = []
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue
    const { href, artifact_id: artifactId } = item as Record<string, unknown>
    if (typeof href !== 'string' || !href || typeof artifactId !== 'string' || !artifactId) continue
    out.push({ href, artifactId })
  }
  return out
}

const OUTBOX_PATH = /(?:^|\/)memory\/outbox\/(?:([^/]+)\/)?([^/?#]+)$/
// First path segments the app itself serves; any other absolute path in a
// reply is a path on the agent's machine.
const APP_ROUTES = /^\/(?:api|rooms|agents|admin|g|inbox|invite|login|topology)(?:[/?#]|$)/

function decode(value: string): string {
  try { return decodeURIComponent(value) } catch { return value }
}

/** A path on the agent's machine rather than an address in the app. */
export function isLocalFileHref(href: string): boolean {
  if (!href || href.startsWith('#') || href.startsWith('?') || href.startsWith('//')) return false
  const scheme = /^([a-z][a-z0-9+.-]*):/i.exec(href)?.[1]?.toLowerCase()
  if (scheme) return scheme === 'file'
  if (href.startsWith('/')) return href !== '/' && !APP_ROUTES.test(href)
  return true
}

function artifactHref(ref: ArtifactReference): string {
  return `/api/v1/rooms/${encodeURIComponent(ref.roomId)}/artifacts/${encodeURIComponent(ref.id)}`
}

/** An agent's reply may link a file by its path on the agent's machine,
 * which is no address in the app.
 *
 * Returns ``null`` for any other link. For a local path it returns the
 * attached artifact's download address — the file the agent attached for
 * that exact link (#1017), else, for an outbox path, the attached file of
 * that name (#964) — or ``{ href: null }`` so the link is shown as text. */
export function resolveLocalFileHref(
  href: string | undefined,
  references: readonly ArtifactReference[],
  linkedFiles: readonly LinkedFile[] = [],
): { href: string | null } | null {
  if (!href || !isLocalFileHref(href)) return null
  const linked = linkedFiles.find(item => item.href === href)
  const linkedRef = linked && references.find(ref => ref.id === linked.artifactId)
  if (linkedRef) return { href: artifactHref(linkedRef) }
  const match = OUTBOX_PATH.exec(href.split(/[?#]/)[0])
  if (match) {
    const roomId = match[1] ? decode(match[1]) : null
    const name = decode(match[2])
    const named = references.filter(ref => ref.name === name)
    const ref = named.find(r => roomId !== null && r.roomId === roomId) ?? named[0]
    if (ref) return { href: artifactHref(ref) }
  }
  return { href: null }
}

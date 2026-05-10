import type { RoomSharedFile } from './roomFiles'

export type SharedFileReferenceOrigin = 'inline' | 'attachment'

export interface SharedFileReference {
  type: 'shared_file'
  id: string
  name: string
  storage_name?: string
  sha256?: string
  origin?: SharedFileReferenceOrigin
}

export interface FileReferenceCandidate {
  id: string
  name: string
  storage_name?: string
  origin?: SharedFileReferenceOrigin
}

export interface ResolvedFileReferenceToken {
  token: string
  suffix: string
  candidate: FileReferenceCandidate
}

export function buildSharedFileReference(
  file: RoomSharedFile,
  origin: SharedFileReferenceOrigin,
): SharedFileReference {
  return {
    type: 'shared_file',
    id: file.id,
    name: file.filename,
    storage_name: file.storage_name,
    sha256: file.sha256,
    origin,
  }
}

const FILE_REF_RE = /(^|\s)\$([^\s$()]+)/g
const TRAILING_PUNCT_RE = /[.,!?;:)\]】。、]+$/
const SHELL_VAR_RE = /^[A-Z_][A-Z0-9_]*$/

function cleanToken(raw: string): string {
  const trailing = raw.match(TRAILING_PUNCT_RE)
  return trailing ? raw.slice(0, -trailing[0].length) : raw
}

function isLikelyShellOrPrice(token: string): boolean {
  return /^\d/.test(token) || SHELL_VAR_RE.test(token)
}

function validOrigin(value: unknown): SharedFileReferenceOrigin | undefined {
  return value === 'inline' || value === 'attachment' ? value : undefined
}

function uniqueMatchingFile(
  token: string,
  files: readonly RoomSharedFile[],
): RoomSharedFile | null {
  const matches = files.filter(
    f => f.filename === token || f.storage_name === token,
  )
  return matches.length === 1 ? matches[0] : null
}

function uniqueMatchingCandidate(
  token: string,
  candidates: readonly FileReferenceCandidate[],
): FileReferenceCandidate | null {
  const matches = candidates.filter(
    c => c.name === token || c.storage_name === token,
  )
  const ids = new Set(matches.map(c => c.id))
  if (ids.size !== 1) return null
  return matches[0] ?? null
}

export function extractSharedFileReferencesFromMetadata(
  metadata?: Record<string, unknown> | null,
): SharedFileReference[] {
  const raw = metadata?.references
  if (!Array.isArray(raw)) return []

  const refs: SharedFileReference[] = []
  const seen = new Set<string>()
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue
    const ref = item as Record<string, unknown>
    if (ref.type !== 'shared_file') continue
    if (typeof ref.id !== 'string' || typeof ref.name !== 'string') continue
    if (seen.has(ref.id)) continue
    seen.add(ref.id)
    const out: SharedFileReference = {
      type: 'shared_file',
      id: ref.id,
      name: ref.name,
    }
    if (typeof ref.storage_name === 'string') out.storage_name = ref.storage_name
    if (typeof ref.sha256 === 'string') out.sha256 = ref.sha256
    const origin = validOrigin(ref.origin)
    if (origin) out.origin = origin
    refs.push(out)
  }
  return refs
}

export function buildFileReferenceCandidates(
  metadata?: Record<string, unknown> | null,
  files: readonly RoomSharedFile[] = [],
): FileReferenceCandidate[] {
  const byId = new Map<string, FileReferenceCandidate>()
  const order: string[] = []

  for (const ref of extractSharedFileReferencesFromMetadata(metadata)) {
    byId.set(ref.id, {
      id: ref.id,
      name: ref.name,
      storage_name: ref.storage_name,
      origin: ref.origin,
    })
    order.push(ref.id)
  }

  for (const file of files) {
    if (byId.has(file.id)) continue
    byId.set(file.id, {
      id: file.id,
      name: file.filename,
      storage_name: file.storage_name,
    })
    order.push(file.id)
  }

  return order.map(id => byId.get(id)!).filter(Boolean)
}

export function resolveFileReferenceToken(
  rawToken: string,
  candidates: readonly FileReferenceCandidate[],
): ResolvedFileReferenceToken | null {
  const token = cleanToken(rawToken)
  const suffix = rawToken.slice(token.length)
  if (!token || isLikelyShellOrPrice(token)) return null
  const candidate = uniqueMatchingCandidate(token, candidates)
  return candidate ? { token, suffix, candidate } : null
}

export function resolveFileReferencesInText(
  content: string,
  files: readonly RoomSharedFile[],
): SharedFileReference[] {
  if (!content || files.length === 0) return []

  const refs: SharedFileReference[] = []
  let match: RegExpExecArray | null
  while ((match = FILE_REF_RE.exec(content)) !== null) {
    const token = cleanToken(match[2])
    if (!token || isLikelyShellOrPrice(token)) continue
    const file = uniqueMatchingFile(token, files)
    if (!file) continue
    refs.push(buildSharedFileReference(file, 'inline'))
  }
  return dedupeSharedFileReferences(refs)
}

export function dedupeSharedFileReferences(
  refs: readonly SharedFileReference[],
): SharedFileReference[] {
  const byId = new Map<string, SharedFileReference>()
  const order: string[] = []
  for (const ref of refs) {
    const existing = byId.get(ref.id)
    if (!existing) {
      byId.set(ref.id, ref)
      order.push(ref.id)
      continue
    }
    if (existing.origin !== 'attachment' && ref.origin === 'attachment') {
      byId.set(ref.id, ref)
    }
  }
  return order.map(id => byId.get(id)!).filter(Boolean)
}

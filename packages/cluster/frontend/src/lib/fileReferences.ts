import type { RoomSharedFile } from './roomFiles'

export type SharedFileReferenceOrigin = 'inline' | 'attachment'

export interface SharedFileReference {
  type: 'shared_file'
  id: string
  name: string
  storage_name: string
  sha256?: string
  origin?: SharedFileReferenceOrigin
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

function uniqueMatchingFile(
  token: string,
  files: readonly RoomSharedFile[],
): RoomSharedFile | null {
  const matches = files.filter(
    f => f.filename === token || f.storage_name === token,
  )
  return matches.length === 1 ? matches[0] : null
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

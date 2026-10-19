/**
 * #908 — file operations on an agent's work files (download, delete,
 * rename, multi-file upload). Each call goes through the agent's machine;
 * the server answers with the refreshed folder snapshot.
 *
 * #914 — on machines that support it, files up to ``transferMax`` and
 * folder zips travel over HTTP with progress instead of one machine frame.
 */
import { apiFetch } from '@/lib/api'
import { getAuthToken } from '@/lib/authStorage'

export const MAX_UPLOAD_BYTES = 1024 * 1024
export const MAX_DOWNLOAD_BYTES = 768 * 1024

/** Where large transfers are possible (#914); absent on older machines. */
export interface TransferLimits {
  transferMax: number
  archiveMax: number
}

export type Progress = (loaded: number, total: number | null) => void

const IMAGE_EXTENSIONS = /\.(png|jpe?g|gif|webp)$/i

export function isImagePath(path: string): boolean {
  return IMAGE_EXTENSIONS.test(path)
}

export function validEntryName(value: string): boolean {
  return value.length > 0 && value.length <= 255 && !['.', '..'].includes(value) && !/[\\/\x00-\x1f\x7f]/.test(value)
}

function endpoint(agentId: string, action: string): string {
  return `/api/v1/agents/${encodeURIComponent(agentId)}/workspace/${action}`
}

/** Fetch a file's bytes; throws ``too_large`` or ``failed``. */
export async function fetchWorkspaceFile(agentId: string, path: string, signal?: AbortSignal): Promise<Blob> {
  const response = await apiFetch(`${endpoint(agentId, 'download')}?path=${encodeURIComponent(path)}`, { signal })
  if (response.status === 413) throw new Error('too_large')
  if (!response.ok) throw new Error('failed')
  return await response.blob()
}

function saveBlob(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob)
  try {
    const link = document.createElement('a')
    link.href = url
    link.download = name
    document.body.appendChild(link)
    link.click()
    link.remove()
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), 0)
  }
}

/** Read a response body while reporting progress. */
async function readWithProgress(response: Response, onProgress?: Progress): Promise<Blob> {
  const total = Number(response.headers.get('content-length')) || null
  if (!response.body || !onProgress) return await response.blob()
  const reader = response.body.getReader()
  const parts: Uint8Array[] = []
  let loaded = 0
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    parts.push(value)
    loaded += value.byteLength
    onProgress(loaded, total)
  }
  return new Blob(parts as BlobPart[], { type: response.headers.get('content-type') ?? undefined })
}

function transferError(status: number): Error {
  if (status === 413) return new Error('too_large')
  if (status === 404) return new Error('not_found')
  if (status === 409) return new Error('unavailable')
  return new Error('failed')
}

export async function downloadWorkspaceFile(
  agentId: string, path: string,
  options: { limits?: TransferLimits | null; onProgress?: Progress } = {},
): Promise<void> {
  const name = path.split('/').pop() ?? 'file'
  if (!options.limits) {
    saveBlob(await fetchWorkspaceFile(agentId, path), name)
    return
  }
  const response = await apiFetch(`${endpoint(agentId, 'download-stream')}?path=${encodeURIComponent(path)}`)
  if (!response.ok) throw transferError(response.status)
  saveBlob(await readWithProgress(response, options.onProgress), name)
}

/** #914 — download a folder ("" = whole workspace) as a zip. */
export async function downloadWorkspaceArchive(
  agentId: string, path: string, fallbackName: string, onProgress?: Progress,
): Promise<void> {
  const response = await apiFetch(`${endpoint(agentId, 'archive')}?path=${encodeURIComponent(path)}`)
  if (!response.ok) throw transferError(response.status)
  const base = path ? path.split('/').pop()! : fallbackName
  saveBlob(await readWithProgress(response, onProgress), `${base}.zip`)
}

/** #914 — stream one file to the machine; resolves to a status string. */
export function uploadLargeFile(
  agentId: string, path: string, file: File, editToken: string, onProgress?: Progress,
): Promise<string> {
  return new Promise(resolve => {
    const request = new XMLHttpRequest()
    const query = `path=${encodeURIComponent(path)}&edit_token=${encodeURIComponent(editToken)}`
    request.open('PUT', `${endpoint(agentId, 'upload-stream')}?${query}`)
    const token = getAuthToken()
    if (token) request.setRequestHeader('Authorization', `Bearer ${token}`)
    request.setRequestHeader('Content-Type', 'application/octet-stream')
    request.upload.onprogress = event => onProgress?.(event.loaded, event.lengthComputable ? event.total : file.size)
    request.onload = () => resolve(
      request.status < 300 ? 'ready'
        : request.status === 409 ? 'conflict'
          : request.status === 413 ? 'too_large'
            : 'failed',
    )
    request.onerror = () => resolve('failed')
    request.send(file)
  })
}

/** POST a change; resolves to the server status (``ready`` on success). */
export async function changeWorkspace(
  agentId: string,
  action: 'delete' | 'rename' | 'upload',
  body: Record<string, string>,
): Promise<string> {
  const response = await apiFetch(endpoint(agentId, action), { method: 'POST', body: JSON.stringify(body) })
  const result = await response.json().catch(() => ({})) as { status?: string }
  if (response.status === 409) return 'conflict'
  if (!response.ok) return result.status ?? 'failed'
  return result.status ?? 'failed'
}

function base64File(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(new Error('read_failed'))
    reader.onload = () => resolve(String(reader.result).split(',', 2)[1] ?? '')
    reader.readAsDataURL(file)
  })
}

export interface UploadOutcome {
  name: string
  status: 'uploaded' | 'skipped' | 'failed'
  reason?: string
}

/**
 * Upload files one at a time into ``folder``. A name that already exists
 * is replaced only when ``confirmReplace`` agrees and the machine can
 * delete files; otherwise it is skipped.
 */
export async function uploadWorkspaceFiles(
  agentId: string,
  folder: string,
  files: File[],
  editToken: string,
  { canReplace, confirmReplace, limits, onProgress }: {
    canReplace: boolean
    confirmReplace: (name: string) => Promise<boolean>
    limits?: TransferLimits | null
    onProgress?: (name: string, loaded: number, total: number | null) => void
  },
): Promise<UploadOutcome[]> {
  const outcomes: UploadOutcome[] = []
  const maxBytes = limits ? limits.transferMax : MAX_UPLOAD_BYTES
  for (const file of files) {
    const path = folder ? `${folder}/${file.name}` : file.name
    if (!validEntryName(file.name)) { outcomes.push({ name: file.name, status: 'failed', reason: 'blocked' }); continue }
    if (file.size > maxBytes) { outcomes.push({ name: file.name, status: 'failed', reason: 'too_large' }); continue }
    let send: () => Promise<string>
    if (limits) {
      send = () => uploadLargeFile(agentId, path, file, editToken, (loaded, total) => onProgress?.(file.name, loaded, total))
    } else {
      let content: string
      try {
        content = await base64File(file)
      } catch {
        outcomes.push({ name: file.name, status: 'failed', reason: 'failed' }); continue
      }
      const body = { path, content_base64: content, edit_token: editToken }
      send = () => changeWorkspace(agentId, 'upload', body)
    }
    let status = await send()
    if (status === 'conflict') {
      if (!canReplace || !(await confirmReplace(file.name))) {
        outcomes.push({ name: file.name, status: 'skipped' }); continue
      }
      const removed = await changeWorkspace(agentId, 'delete', { path, edit_token: editToken })
      status = removed === 'ready' ? await send() : removed
    }
    outcomes.push(status === 'ready'
      ? { name: file.name, status: 'uploaded' }
      : { name: file.name, status: 'failed', reason: status })
  }
  return outcomes
}

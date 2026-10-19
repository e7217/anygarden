/**
 * #908 — file operations on an agent's work files (download, delete,
 * rename, multi-file upload). Each call goes through the agent's machine;
 * the server answers with the refreshed folder snapshot.
 */
import { apiFetch } from '@/lib/api'

export const MAX_UPLOAD_BYTES = 1024 * 1024
export const MAX_DOWNLOAD_BYTES = 768 * 1024

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

export async function downloadWorkspaceFile(agentId: string, path: string): Promise<void> {
  const blob = await fetchWorkspaceFile(agentId, path)
  const url = URL.createObjectURL(blob)
  try {
    const link = document.createElement('a')
    link.href = url
    link.download = path.split('/').pop() ?? 'file'
    document.body.appendChild(link)
    link.click()
    link.remove()
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), 0)
  }
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
  { canReplace, confirmReplace }: { canReplace: boolean; confirmReplace: (name: string) => Promise<boolean> },
): Promise<UploadOutcome[]> {
  const outcomes: UploadOutcome[] = []
  for (const file of files) {
    const path = folder ? `${folder}/${file.name}` : file.name
    if (!validEntryName(file.name)) { outcomes.push({ name: file.name, status: 'failed', reason: 'blocked' }); continue }
    if (file.size > MAX_UPLOAD_BYTES) { outcomes.push({ name: file.name, status: 'failed', reason: 'too_large' }); continue }
    let content: string
    try {
      content = await base64File(file)
    } catch {
      outcomes.push({ name: file.name, status: 'failed', reason: 'failed' }); continue
    }
    const body = { path, content_base64: content, edit_token: editToken }
    let status = await changeWorkspace(agentId, 'upload', body)
    if (status === 'conflict') {
      if (!canReplace || !(await confirmReplace(file.name))) {
        outcomes.push({ name: file.name, status: 'skipped' }); continue
      }
      const removed = await changeWorkspace(agentId, 'delete', { path, edit_token: editToken })
      status = removed === 'ready' ? await changeWorkspace(agentId, 'upload', body) : removed
    }
    outcomes.push(status === 'ready'
      ? { name: file.name, status: 'uploaded' }
      : { name: file.name, status: 'failed', reason: status })
  }
  return outcomes
}

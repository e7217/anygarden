/**
 * Client helpers for the ``/api/v1/rooms/{room_id}/files`` endpoints
 * (#246). Upload uses ``multipart/form-data`` so the browser fills in
 * the boundary automatically — ``lib/api.ts``'s default
 * ``Content-Type: application/json`` would corrupt the payload, so
 * this file issues ``fetch`` directly and only reuses the bearer
 * token from ``localStorage``.
 */

export interface RoomSharedFile {
  id: string
  room_id: string
  filename: string
  storage_name: string
  sha256: string
  size_bytes: number
  mime: string
  uploaded_by: string | null
  created_at: string
}

function authHeaders(): Record<string, string> {
  const token = localStorage.getItem('anygarden_token')
  return token ? { Authorization: `Bearer ${token}` } : {}
}

/** Upload rejection carrying the HTTP status, so callers can tell an
 * oversize (413) or disallowed (415) file from other failures. */
export class RoomFileUploadError extends Error {
  readonly status: number

  constructor(status: number, detail: string) {
    super(detail || `Upload failed (HTTP ${status})`)
    this.name = 'RoomFileUploadError'
    this.status = status
  }
}

export async function uploadRoomFile(
  roomId: string,
  file: File,
): Promise<RoomSharedFile> {
  const fd = new FormData()
  fd.append('upload', file)
  const resp = await fetch(`/api/v1/rooms/${roomId}/files`, {
    method: 'POST',
    headers: authHeaders(),
    body: fd,
  })
  if (!resp.ok) {
    throw new RoomFileUploadError(resp.status, await resp.text())
  }
  return resp.json()
}

export async function listRoomFiles(
  roomId: string,
): Promise<RoomSharedFile[]> {
  const resp = await fetch(`/api/v1/rooms/${roomId}/files`, {
    method: 'GET',
    headers: authHeaders(),
  })
  if (!resp.ok) {
    throw new Error(`List failed (HTTP ${resp.status})`)
  }
  return resp.json()
}

export async function deleteRoomFile(
  roomId: string,
  fileId: string,
): Promise<void> {
  const resp = await fetch(`/api/v1/rooms/${roomId}/files/${fileId}`, {
    method: 'DELETE',
    headers: authHeaders(),
  })
  if (!resp.ok && resp.status !== 204) {
    throw new Error(`Delete failed (HTTP ${resp.status})`)
  }
}

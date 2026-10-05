/**
 * Client helpers for the ``/api/v1/rooms/{id}/artifacts`` endpoints
 * (#290 Phase B). Artifact bytes always use authenticated fetch, followed
 * by a browser blob URL for previews or downloads.
 */

export interface RoomArtifact {
  id: string
  room_id: string
  produced_by_agent_id: string | null
  filename: string
  sha256: string
  size_bytes: number
  mime: string
  created_at: string
}

function authHeaders(): Record<string, string> {
  const token = localStorage.getItem('anygarden_token')
  return token ? { Authorization: `Bearer ${token}` } : {}
}

export async function listRoomArtifacts(
  roomId: string,
): Promise<RoomArtifact[]> {
  const resp = await fetch(`/api/v1/rooms/${roomId}/artifacts`, {
    method: 'GET',
    headers: authHeaders(),
  })
  if (!resp.ok) {
    throw new Error(`List failed (HTTP ${resp.status})`)
  }
  return resp.json()
}

export async function deleteRoomArtifact(
  roomId: string,
  artifactId: string,
): Promise<void> {
  const resp = await fetch(
    `/api/v1/rooms/${roomId}/artifacts/${artifactId}`,
    { method: 'DELETE', headers: authHeaders() },
  )
  if (!resp.ok && resp.status !== 204) {
    throw new Error(`Delete failed (HTTP ${resp.status})`)
  }
}

/** Server URL (no auth on the URL itself — needs Bearer header). */
export function artifactDownloadUrl(
  roomId: string,
  artifactId: string,
): string {
  return `/api/v1/rooms/${roomId}/artifacts/${artifactId}`
}

/** Fetch the artifact bytes and wrap them in a blob: URL suitable
 * for ``<img src=...>`` or anchor downloads. Returns the URL plus a
 * disposer the caller MUST run on unmount to free the underlying
 * Blob — Chrome leaks ~tens of MB per orphaned URL.
 */
export async function fetchArtifactBlobUrl(
  roomId: string,
  artifactId: string,
): Promise<{ url: string; filename?: string; revoke: () => void }> {
  const resp = await fetch(artifactDownloadUrl(roomId, artifactId), {
    method: 'GET',
    headers: authHeaders(),
  })
  if (!resp.ok) {
    throw new Error(`Download failed (HTTP ${resp.status})`)
  }
  const blob = await resp.blob()
  const url = URL.createObjectURL(blob)
  const disposition = resp.headers?.get('Content-Disposition') ?? ''
  const encodedName = /filename\*=UTF-8''([^;]+)/i.exec(disposition)?.[1]
  let filename = /filename="([^"]+)"|filename=([^;]+)/i.exec(disposition)?.slice(1).find(Boolean)?.trim()
  if (encodedName) {
    try { filename = decodeURIComponent(encodedName) } catch { /* Keep the plain filename. */ }
  }
  filename = filename?.split(/[\\/]/).pop()?.replace(/[\x00-\x1f]/g, '')
  return { url, filename, revoke: () => URL.revokeObjectURL(url) }
}

/** Start a browser download and release the blob after the browser consumes it. */
export function startArtifactDownload(download: { url: string; revoke: () => void }, filename: string): void {
  const anchor = document.createElement('a')
  anchor.href = download.url
  anchor.download = filename
  document.body.appendChild(anchor)
  try { anchor.click() } finally { anchor.remove() }
  window.setTimeout(download.revoke, 1000)
}

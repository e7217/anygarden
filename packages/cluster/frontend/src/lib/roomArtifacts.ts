/**
 * Client helpers for the ``/api/v1/rooms/{id}/artifacts`` endpoints
 * (#290 Phase B). The download path uses the URL directly (so
 * ``<img src=...>`` works) — only list and delete need an explicit
 * fetch wrapper here.
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
  const token = localStorage.getItem('doorae_token')
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
): Promise<{ url: string; revoke: () => void }> {
  const resp = await fetch(artifactDownloadUrl(roomId, artifactId), {
    method: 'GET',
    headers: authHeaders(),
  })
  if (!resp.ok) {
    throw new Error(`Download failed (HTTP ${resp.status})`)
  }
  const blob = await resp.blob()
  const url = URL.createObjectURL(blob)
  return { url, revoke: () => URL.revokeObjectURL(url) }
}

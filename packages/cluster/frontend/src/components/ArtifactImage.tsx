import { useEffect, useState } from 'react'
import { fetchArtifactBlobUrl } from '@/lib/roomArtifacts'

export type ArtifactBlobState =
  | { status: 'loading' }
  | { status: 'ready'; src: string }
  | { status: 'failed' }

/** Fetch artifact bytes through the authenticated endpoint and expose them
 * as a blob: URL, revoked on unmount or when the artifact changes. */
export function useArtifactBlobUrl(roomId: string, artifactId: string): ArtifactBlobState {
  const [state, setState] = useState<{ key: string; value: ArtifactBlobState }>({
    key: `${roomId}/${artifactId}`,
    value: { status: 'loading' },
  })
  const key = `${roomId}/${artifactId}`
  useEffect(() => {
    let cancelled = false
    let cleanup: (() => void) | null = null
    fetchArtifactBlobUrl(roomId, artifactId)
      .then(({ url, revoke }) => {
        if (cancelled) {
          revoke()
        } else {
          cleanup = revoke
          setState({ key, value: { status: 'ready', src: url } })
        }
      })
      .catch(() => {
        if (!cancelled) setState({ key, value: { status: 'failed' } })
      })
    return () => {
      cancelled = true
      if (cleanup) cleanup()
    }
  }, [roomId, artifactId, key])
  return state.key === key ? state.value : { status: 'loading' }
}

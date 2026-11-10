import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { apiFetch } from '@/lib/api'
import type { ChatMessage } from '@/hooks/useWebSocket'
import type { PeerChain } from '@/lib/peerChains'

/** Poll interval while a chain in the room is running (#1006). */
export const PEER_CHAINS_POLL_MS = 3_000

/** Chains fetched per room; the newest are the ones a reader acts on. */
export const PEER_CHAINS_LIMIT = 20

/**
 * #1006 — the room's agent question chains.
 *
 * There is no push event for chain progress, so the hook fetches when
 * the room opens and whenever something hints a chain moved — a working
 * agent starts or stops, or a new question message arrives — and polls
 * every ``PEER_CHAINS_POLL_MS`` while any chain is still active. Polling
 * stops on its own once every chain has finished.
 *
 * ``roomId`` null disables the hook.
 */
export function usePeerChains(
  roomId: string | null,
  messages: ChatMessage[],
  workingIds: Iterable<string>,
) {
  const [chains, setChains] = useState<PeerChain[]>([])
  const workingKey = Array.from(workingIds).sort().join(',')
  // The latest question message is enough of a signal: a new chain or a
  // deeper question always posts one.
  const lastQuestionId = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].metadata?.kind === 'peer_query') return messages[i].id
    }
    return ''
  }, [messages])

  const roomRef = useRef(roomId)
  const controllerRef = useRef<AbortController | null>(null)

  const refresh = useCallback(async () => {
    const room = roomRef.current
    if (!room) return
    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller
    try {
      const resp = await apiFetch(
        `/api/v1/rooms/${encodeURIComponent(room)}/peer-chains?limit=${PEER_CHAINS_LIMIT}`,
        { signal: controller.signal },
      )
      if (!resp.ok) return
      const body = await resp.json() as { chains?: PeerChain[] }
      if (controller.signal.aborted || roomRef.current !== room) return
      setChains(Array.isArray(body?.chains) ? body.chains : [])
    } catch {
      // Keep the last known chains; the next poll corrects them.
    }
  }, [])

  // Declared before the fetch effect so the ref already names the new
  // room when that effect runs in the same commit.
  useEffect(() => {
    roomRef.current = roomId
    setChains([])
    return () => controllerRef.current?.abort()
  }, [roomId])

  useEffect(() => {
    if (!roomId) return
    void refresh()
  }, [roomId, workingKey, lastQuestionId, refresh])

  const anyActive = chains.some(chain => chain.state === 'active')
  useEffect(() => {
    if (!roomId || !anyActive) return
    const refreshIfVisible = () => {
      if (document.visibilityState === 'visible') void refresh()
    }
    const interval = window.setInterval(refreshIfVisible, PEER_CHAINS_POLL_MS)
    document.addEventListener('visibilitychange', refreshIfVisible)
    return () => {
      window.clearInterval(interval)
      document.removeEventListener('visibilitychange', refreshIfVisible)
    }
  }, [roomId, anyActive, refresh])

  /** Stops every open turn of a chain; resolves true when the server accepted. */
  const cancel = useCallback(async (chainId: string): Promise<boolean> => {
    const room = roomRef.current
    if (!room) return false
    try {
      const resp = await apiFetch(
        `/api/v1/rooms/${encodeURIComponent(room)}/peer-chains/${encodeURIComponent(chainId)}/cancel`,
        { method: 'POST' },
      )
      if (!resp.ok) return false
      setChains(previous => previous.map(chain => (chain.id === chainId ? { ...chain, state: 'cancelled' } : chain)))
      void refresh()
      return true
    } catch {
      return false
    }
  }, [refresh])

  return { chains, cancel, refresh }
}

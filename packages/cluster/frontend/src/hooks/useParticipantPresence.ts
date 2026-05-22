import { useEffect, useState, useCallback } from 'react'

/**
 * Per-participant presence state maintained in realtime (#54).
 *
 * Input comes from two sources:
 *   1. REST ``GET /rooms/{id}`` on mount — supplies the initial
 *      snapshot via ``seed``.
 *   2. WS ``presence_update`` frames — rebroadcast by
 *      ``useWebSocket`` on the ``anygarden:presence:update`` window
 *      event. We listen once per ``roomId`` and merge by
 *      ``participant_id``.
 *
 * Why window-event plumbing? The WS hook lives on the same page but
 * can't reach the presence hook's setState directly; a context
 * provider would work but would force a bigger refactor just for
 * one frame type. Membership and pin-order events already use this
 * pattern (see ``useWebSocket.ts``), so we follow the same shape.
 */
export interface PresenceEntry {
  online: boolean
  lastSeenAt: string | null
}

export interface PresencePatch {
  room_id: string
  participant_id: string
  online: boolean
  last_seen_at?: string | null
}

export type PresenceMap = Record<string, PresenceEntry>

/**
 * Pure merge helper — extracted so it can be unit-tested without the
 * DOM ``window`` listener wiring.
 */
export function mergePresencePatch(
  prev: PresenceMap,
  patch: PresencePatch,
): PresenceMap {
  const current = prev[patch.participant_id]
  const next: PresenceEntry = {
    online: patch.online,
    lastSeenAt: patch.last_seen_at ?? null,
  }
  // Skip state churn if nothing actually changed — avoids a
  // pointless re-render of every presence consumer.
  if (
    current &&
    current.online === next.online &&
    current.lastSeenAt === next.lastSeenAt
  ) {
    return prev
  }
  return { ...prev, [patch.participant_id]: next }
}

export interface PresenceSeedItem {
  id: string
  online?: boolean
  last_seen_at?: string | null
}

export function useParticipantPresence(
  roomId: string | null,
  seed: PresenceSeedItem[],
): PresenceMap {
  const [state, setState] = useState<PresenceMap>(() => buildSeed(seed))

  // Re-seed whenever the room changes or the REST snapshot arrives
  // with new participants (e.g. add_participant flows). The ``seed``
  // identity changes only when the parent rebuilds it, so this is
  // cheap.
  useEffect(() => {
    setState(buildSeed(seed))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roomId, seed])

  const onEvent = useCallback(
    (e: Event) => {
      const detail = (e as CustomEvent).detail as PresencePatch | undefined
      if (!detail || !roomId) return
      if (detail.room_id !== roomId) return
      setState(prev => mergePresencePatch(prev, detail))
    },
    [roomId],
  )

  useEffect(() => {
    window.addEventListener('anygarden:presence:update', onEvent)
    return () => window.removeEventListener('anygarden:presence:update', onEvent)
  }, [onEvent])

  return state
}

function buildSeed(seed: PresenceSeedItem[]): PresenceMap {
  const out: PresenceMap = {}
  for (const p of seed) {
    out[p.id] = {
      online: Boolean(p.online),
      lastSeenAt: p.last_seen_at ?? null,
    }
  }
  return out
}

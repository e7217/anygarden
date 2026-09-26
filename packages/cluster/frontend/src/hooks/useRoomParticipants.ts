import { useCallback, useEffect, useRef, useState } from 'react'
import { apiFetch } from '@/lib/api'
import type { Participant } from '@/lib/participants'

type Snapshot = {
  roomId: string | null
  name: string
  participants: Record<string, Participant>
  loading: boolean
  loaded: boolean
  errorStatus: number | null
}
const EMPTY: Snapshot = { roomId: null, name: '', participants: {}, loading: false, loaded: false, errorStatus: null }

function participant(raw: Participant): Participant {
  return {
    ...raw,
    display_name: raw.display_name ?? raw.id.slice(0, 8),
    kind: raw.kind === 'agent' ? 'agent' : 'user',
    is_anonymous: raw.kind === 'guest' || Boolean(raw.is_anonymous),
    description: raw.kind === 'agent' ? raw.description ?? null : null,
  }
}

/** Public room roster: preserves REST identity fields when slim WS updates arrive. */
export function useRoomParticipants(roomId: string | null) {
  const currentRoom = useRef(roomId)
  currentRoom.current = roomId
  const generation = useRef(0)
  const controller = useRef<AbortController | null>(null)
  const presenceVersion = useRef(0)
  const presence = useRef<Record<string, { version: number; online: boolean; last_seen_at: string | null }>>({})
  const [state, setState] = useState<Snapshot>(EMPTY)

  const refresh = useCallback(async () => {
    const id = currentRoom.current
    if (!id) return
    controller.current?.abort()
    const request = new AbortController()
    controller.current = request
    const version = ++generation.current
    const startedPresence = presenceVersion.current
    const isCurrent = () => currentRoom.current === id && generation.current === version && !request.signal.aborted
    setState(previous => ({ ...(previous.roomId === id ? previous : EMPTY), roomId: id, loading: true, errorStatus: null }))
    let status = 0
    try {
      const response = await apiFetch(`/api/v1/rooms/${id}`, { signal: request.signal })
      if (!response.ok) {
        status = response.status
        throw new Error('Room unavailable')
      }
      const room = await response.json()
      if (!isCurrent()) return
      const participants: Record<string, Participant> = {}
      for (const raw of room.participants ?? []) {
        const next = participant(raw)
        const update = presence.current[next.id]
        if (update && update.version > startedPresence) {
          next.online = update.online
          next.last_seen_at = update.last_seen_at
        }
        participants[next.id] = next
      }
      setState({ roomId: id, name: room.name ?? '', participants, loading: false, loaded: true, errorStatus: null })
    } catch {
      if (isCurrent()) setState(previous => ({ ...previous, loading: false, errorStatus: status || 503 }))
    }
  }, [])

  useEffect(() => {
    currentRoom.current = roomId
    presence.current = {}
    void refresh()
    function onRoster(event: Event) {
      const detail = (event as CustomEvent).detail
      if (!roomId || detail?.room_id !== roomId || currentRoom.current !== roomId) return
      if (Array.isArray(detail.participants)) {
        setState(previous => {
          const base = previous.roomId === roomId ? previous : EMPTY
          const participants: Record<string, Participant> = {}
          for (const raw of detail.participants) {
            participants[raw.id] = participant({ ...base.participants[raw.id], ...raw })
          }
          return { ...base, roomId, participants }
        })
      }
      // The roster event follows the DB commit. A fresh GET fills role/avatar
      // fields for new members; invalidate any pre-event response immediately.
      void refresh()
    }
    function onPresence(event: Event) {
      const detail = (event as CustomEvent).detail
      if (!roomId || detail?.room_id !== roomId || currentRoom.current !== roomId) return
      const update = { version: ++presenceVersion.current, online: Boolean(detail.online), last_seen_at: detail.last_seen_at ?? null }
      presence.current[detail.participant_id] = update
      setState(previous => {
        const found = previous.roomId === roomId && previous.participants[detail.participant_id]
        if (!found) return previous
        return { ...previous, participants: { ...previous.participants,
          [found.id]: { ...found, online: update.online, last_seen_at: update.last_seen_at },
        } }
      })
    }
    window.addEventListener('anygarden:rooms:settings-changed', onRoster)
    window.addEventListener('anygarden:presence:update', onPresence)
    return () => {
      currentRoom.current = null
      ++generation.current
      controller.current?.abort()
      window.removeEventListener('anygarden:rooms:settings-changed', onRoster)
      window.removeEventListener('anygarden:presence:update', onPresence)
    }
  }, [roomId, refresh])

  const current = state.roomId === roomId ? state : EMPTY
  return { ...current, refresh }
}

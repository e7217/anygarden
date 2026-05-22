import { useEffect, useState } from 'react'
import { useRightSidebarLayout } from '@/hooks/useRightSidebarLayout'

/**
 * Boolean signal for "new context items arrived while the right rail
 * was closed" (#302).
 *
 * Starts ``false``. Flips to ``true`` when a ``anygarden:task:updated``
 * event for the active room fires *and* the rail is currently
 * collapsed. Resets to ``false`` whenever the rail flips open.
 *
 * The signal is intentionally a single boolean — a counter would
 * require defining "new" precisely (creates only? state changes? for
 * how long?) and clutter the toggle's chrome. A dot is enough:
 * "there's something to look at if you bother."
 */
export function useRightRailNotice(roomId: string | null): boolean {
  const { collapsed } = useRightSidebarLayout()
  const [hasNotice, setHasNotice] = useState(false)

  // Drop the dot the moment the user opens the rail. We don't try to
  // remember "they saw it but didn't scroll" — the rail's own scroll
  // and badge cues take over once it's visible.
  useEffect(() => {
    if (!collapsed) setHasNotice(false)
  }, [collapsed])

  useEffect(() => {
    if (!roomId) return
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail as
        | { task?: { room_id?: string } }
        | undefined
      if (!detail?.task) return
      if (detail.task.room_id && detail.task.room_id !== roomId) return
      // Only signal when the rail is actually closed — open users
      // see the change live and don't need a dot.
      if (collapsed) setHasNotice(true)
    }
    window.addEventListener('anygarden:task:updated', handler)
    return () => window.removeEventListener('anygarden:task:updated', handler)
  }, [roomId, collapsed])

  return hasNotice
}

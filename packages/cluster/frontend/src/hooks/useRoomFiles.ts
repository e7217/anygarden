import { useState, useEffect, useCallback } from 'react'
import {
  listRoomFiles,
  uploadRoomFile,
  deleteRoomFile,
  type RoomSharedFile,
} from '@/lib/roomFiles'

export type { RoomSharedFile }

export interface UseRoomFilesValue {
  files: RoomSharedFile[]
  loading: boolean
  error: string | null
  refresh: () => Promise<void>
  upload: (file: File) => Promise<RoomSharedFile | null>
  remove: (fileId: string) => Promise<void>
}

/**
 * Subscribe to a room's shared files (#246 / #302).
 *
 * Wraps the bare REST helpers in ``lib/roomFiles.ts`` with React state
 * + an idempotent refresh handle so the legacy ``RoomSharedFilesDialog``
 * and the new right-rail ``FilesSection`` share one cache shape and
 * one error model. Pass ``roomId === null`` to suspend.
 */
export function useRoomFiles(roomId: string | null): UseRoomFilesValue {
  const [files, setFiles] = useState<RoomSharedFile[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    if (!roomId) {
      setFiles([])
      return
    }
    setLoading(true)
    setError(null)
    try {
      setFiles(await listRoomFiles(roomId))
    } catch (e) {
      setFiles([])
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [roomId])

  useEffect(() => {
    refresh()
  }, [refresh])

  const upload = useCallback<UseRoomFilesValue['upload']>(
    async (file) => {
      if (!roomId) return null
      try {
        const created = await uploadRoomFile(roomId, file)
        await refresh()
        return created
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
        return null
      }
    },
    [roomId, refresh],
  )

  const remove = useCallback<UseRoomFilesValue['remove']>(
    async (fileId) => {
      if (!roomId) return
      try {
        await deleteRoomFile(roomId, fileId)
        // Optimistic local prune — avoids a network round-trip for the
        // common case of a single delete in a list of N.
        setFiles((prev) => prev.filter((f) => f.id !== fileId))
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      }
    },
    [roomId],
  )

  return { files, loading, error, refresh, upload, remove }
}

import { useCallback, useEffect, useState } from 'react'
import { Download, FileText, Image as ImageIcon, Trash2 } from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import {
  artifactDownloadUrl,
  deleteRoomArtifact,
  fetchArtifactBlobUrl,
  listRoomArtifacts,
  type RoomArtifact,
} from '@/lib/roomArtifacts'

interface RoomArtifactsDialogProps {
  roomId: string | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function isImage(mime: string): boolean {
  return mime.startsWith('image/')
}

/** Renders an image artifact inline by fetching the bytes through the
 * authenticated Bearer endpoint and wrapping them in a blob: URL.
 * Falls back to a generic icon while loading or on auth failure. */
function ArtifactImagePreview({
  roomId,
  artifactId,
  alt,
}: {
  roomId: string
  artifactId: string
  alt: string
}) {
  const [src, setSrc] = useState<string | null>(null)
  useEffect(() => {
    let cancelled = false
    let cleanup: (() => void) | null = null
    fetchArtifactBlobUrl(roomId, artifactId)
      .then(({ url, revoke }) => {
        if (cancelled) {
          revoke()
        } else {
          setSrc(url)
          cleanup = revoke
        }
      })
      .catch(() => {
        if (!cancelled) setSrc(null)
      })
    return () => {
      cancelled = true
      if (cleanup) cleanup()
    }
  }, [roomId, artifactId])
  if (!src) {
    return (
      <div className="flex h-32 w-full items-center justify-center rounded-[var(--radius-sm)] bg-black/[0.03]">
        <ImageIcon className="h-6 w-6 text-[var(--color-foreground-subtle)]" />
      </div>
    )
  }
  return (
    <img
      src={src}
      alt={alt}
      className="h-32 w-full rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-black/[0.02] object-contain"
    />
  )
}

/** #290 Phase B — gallery-style view of every artifact an agent has
 * dropped into the room's outbox. Image MIMEs render an inline
 * preview; text/* and unknown types fall back to a file-icon card
 * with a download link. Delete removes the disk blob and broadcasts
 * ``room_artifact.removed`` so other subscribers refresh. */
export default function RoomArtifactsDialog({
  roomId,
  open,
  onOpenChange,
}: RoomArtifactsDialogProps) {
  const [items, setItems] = useState<RoomArtifact[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    if (!roomId) return
    setLoading(true)
    setError(null)
    try {
      setItems(await listRoomArtifacts(roomId))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [roomId])

  useEffect(() => {
    if (open) void refresh()
  }, [open, refresh])

  // Re-fetch when the WS layer signals a change in this room.
  useEffect(() => {
    if (!open || !roomId) return
    function handler(e: Event) {
      const detail = (e as CustomEvent).detail as { artifact?: { room_id?: string }; room_id?: string }
      const eventRoomId = detail?.artifact?.room_id ?? detail?.room_id
      if (eventRoomId === roomId) void refresh()
    }
    window.addEventListener('anygarden:room_artifact:added', handler)
    window.addEventListener('anygarden:room_artifact:removed', handler)
    return () => {
      window.removeEventListener('anygarden:room_artifact:added', handler)
      window.removeEventListener('anygarden:room_artifact:removed', handler)
    }
  }, [open, roomId, refresh])

  const handleDelete = async (artifactId: string) => {
    if (!roomId) return
    if (!confirm('이 산출물을 룸에서 제거할까요?')) return
    try {
      await deleteRoomArtifact(roomId, artifactId)
      setItems(prev => prev.filter(i => i.id !== artifactId))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ImageIcon className="h-4 w-4 text-[var(--color-foreground-muted)]" />
            산출물
          </DialogTitle>
          <DialogDescription>
            에이전트가 <code>memory/outbox/</code>에 떨군 파일이 여기에 모입니다.
            이미지는 미리보기로, 그 외 파일은 다운로드로 확인할 수 있어요.
          </DialogDescription>
        </DialogHeader>
        {error && (
          <p className="text-xs text-[var(--color-destructive)]" role="alert">
            {error}
          </p>
        )}
        {loading ? (
          <p className="text-sm text-[var(--color-foreground-subtle)]">
            불러오는 중...
          </p>
        ) : items.length === 0 ? (
          <p className="text-sm text-[var(--color-foreground-subtle)]">
            아직 에이전트가 만든 산출물이 없습니다.
          </p>
        ) : (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {items.map(item => {
              const downloadUrl = artifactDownloadUrl(roomId!, item.id)
              return (
                <div
                  key={item.id}
                  className="flex flex-col gap-2 rounded-[var(--radius-md)] border border-[var(--color-border)] p-3"
                >
                  {isImage(item.mime) && roomId ? (
                    <ArtifactImagePreview
                      roomId={roomId}
                      artifactId={item.id}
                      alt={item.filename}
                    />
                  ) : (
                    <div className="flex h-32 w-full items-center justify-center rounded-[var(--radius-sm)] bg-black/[0.03]">
                      <FileText className="h-6 w-6 text-[var(--color-foreground-subtle)]" />
                    </div>
                  )}
                  <div className="min-w-0">
                    <p
                      className="truncate text-sm text-[var(--color-foreground)]"
                      title={item.filename}
                    >
                      {item.filename}
                    </p>
                    <p className="text-[11px] text-[var(--color-foreground-subtle)]">
                      {formatBytes(item.size_bytes)} · {item.mime}
                    </p>
                  </div>
                  <div className="flex justify-end gap-1">
                    <a
                      href={downloadUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 rounded-[var(--radius-xs)] px-2 py-1 text-xs text-[var(--color-foreground-muted)] hover:bg-black/5"
                      title="다운로드"
                    >
                      <Download className="h-3.5 w-3.5" />
                    </a>
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => handleDelete(item.id)}
                      aria-label={`Delete ${item.filename}`}
                      title="삭제"
                    >
                      <Trash2 className="h-4 w-4 text-[var(--color-foreground-subtle)]" />
                    </Button>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

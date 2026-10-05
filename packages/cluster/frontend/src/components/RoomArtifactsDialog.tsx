import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Download, FileText, Image as ImageIcon, Trash2 } from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { useLocale } from '@/i18n/LocaleProvider'
import { useFeedback } from '@/components/feedback/FeedbackProvider'
import {
  deleteRoomArtifact,
  fetchArtifactBlobUrl,
  listRoomArtifacts,
  startArtifactDownload,
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
      <div className="flex h-32 w-full items-center justify-center rounded-[var(--radius-sm)] bg-[var(--color-surface-alt)]">
        <ImageIcon className="h-6 w-6 text-[var(--color-foreground-subtle)]" />
      </div>
    )
  }
  return (
    <img
      src={src}
      alt={alt}
      loading="lazy"
      decoding="async"
      className="h-32 w-full rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-[var(--color-surface-alt)] object-contain"
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
  const { t } = useLocale()
  const { confirm } = useFeedback()
  const scope = useMemo(() => ({ roomId, open, active: true, request: 0, downloading: false }), [roomId, open])
  const currentScope = useRef(scope)
  currentScope.current = scope
  const [snapshot, setSnapshot] = useState<{
    scope: typeof scope; items: RoomArtifact[]; loading: boolean; error: string | null; downloading: boolean
  }>(() => ({ scope, items: [], loading: Boolean(open && roomId), error: null, downloading: false }))
  const isCurrent = useCallback(() => Boolean(roomId && open && scope.active && currentScope.current === scope), [roomId, open, scope])
  const { items, loading, error, downloading } = snapshot.scope === scope && open
    ? snapshot
    : { items: [], loading: Boolean(open && roomId), error: null, downloading: false }

  const refresh = useCallback(async () => {
    if (!roomId || !isCurrent()) return
    const request = ++scope.request
    const accepts = () => isCurrent() && request === scope.request
    setSnapshot(previous => ({ scope, items: previous.scope === scope ? previous.items : [], loading: true, error: null, downloading: scope.downloading }))
    try {
      const items = await listRoomArtifacts(roomId)
      if (accepts()) setSnapshot({ scope, items, loading: false, error: null, downloading: scope.downloading })
    } catch (err) {
      if (accepts()) setSnapshot(previous => ({ ...previous, loading: false, error: err instanceof Error ? err.message : String(err) }))
    }
  }, [roomId, scope, isCurrent])

  useEffect(() => {
    scope.active = true
    void refresh()
    return () => { scope.active = false }
  }, [scope, refresh])

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
    if (!roomId || !isCurrent()) return
    if (!await confirm({ title: t('rooms.removeArtifact'), description: t('rooms.removeArtifactConfirm'), confirmLabel: t('rooms.removeArtifact'), destructive: true })) return
    if (!isCurrent()) return
    try {
      await deleteRoomArtifact(roomId, artifactId)
      if (isCurrent()) {
        // A list fetched before deletion cannot restore the removed artifact.
        ++scope.request
        setSnapshot(previous => ({ ...previous, loading: false, items: previous.items.filter(i => i.id !== artifactId) }))
      }
    } catch (err) {
      if (isCurrent()) setSnapshot(previous => ({ ...previous, error: err instanceof Error ? err.message : String(err) }))
    }
  }

  const handleDownload = async (item: RoomArtifact) => {
    if (!roomId || !isCurrent() || scope.downloading) return
    scope.downloading = true
    setSnapshot(previous => ({ ...previous, downloading: true, error: null }))
    let download: Awaited<ReturnType<typeof fetchArtifactBlobUrl>> | null = null
    try {
      download = await fetchArtifactBlobUrl(roomId, item.id)
      if (!isCurrent()) return
      startArtifactDownload(download, item.filename)
      download = null
    } catch (err) {
      if (isCurrent()) setSnapshot(previous => ({ ...previous, error: err instanceof Error ? err.message : String(err) }))
    } finally {
      download?.revoke()
      scope.downloading = false
      if (isCurrent()) setSnapshot(previous => ({ ...previous, downloading: false }))
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[min(90dvh,52rem)] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ImageIcon className="h-4 w-4 text-[var(--color-foreground-muted)]" />
            {t('rooms.artifacts')}
          </DialogTitle>
          <DialogDescription>
            {t('rooms.artifactsDescription')}
          </DialogDescription>
        </DialogHeader>
        {error && (
          <p className="text-xs text-[var(--color-destructive)]" role="alert">
            {error}
          </p>
        )}
        {loading ? (
          <p className="text-sm text-[var(--color-foreground-subtle)]">
            {t('common.loading')}
          </p>
        ) : items.length === 0 ? (
          <p className="text-sm text-[var(--color-foreground-subtle)]">
            {t('rooms.noArtifacts')}
          </p>
        ) : (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {items.map(item => (
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
                    <div className="flex h-32 w-full items-center justify-center rounded-[var(--radius-sm)] bg-[var(--color-surface-alt)]">
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
                    <p className="text-badge font-normal text-[var(--color-foreground-subtle)]">
                      {formatBytes(item.size_bytes)} · {item.mime}
                    </p>
                    {item.produced_by_agent_id && (
                      <p className="mt-1 break-all text-xs text-[var(--color-foreground-muted)]">
                        {t('rooms.agent')}: {item.produced_by_agent_id}
                      </p>
                    )}
                    <p className="mt-1 truncate font-mono text-xs text-[var(--color-foreground-subtle)]" title={item.sha256}>
                      SHA-256: {item.sha256.slice(0, 12)}…
                    </p>
                  </div>
                  <div className="flex justify-end gap-2">
                    <Button
                      variant="ghost"
                      size="icon"
                      disabled={downloading}
                      onClick={() => void handleDownload(item)}
                      title={t('rooms.download')}
                      aria-label={t('rooms.download')}
                    >
                      <Download className="h-3.5 w-3.5" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => handleDelete(item.id)}
                      aria-label={t('rooms.deleteArtifact', { name: item.filename })}
                      title={t('rooms.removeArtifact')}
                    >
                      <Trash2 className="h-4 w-4 text-[var(--color-foreground-subtle)]" />
                    </Button>
                  </div>
                </div>
              ))}
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

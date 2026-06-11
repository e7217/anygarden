import { useEffect } from 'react'
import { FileText, Paperclip, Trash2 } from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { useRoomFiles } from '@/hooks/useRoomFiles'

interface RoomSharedFilesDialogProps {
  roomId: string | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/** #246 — lightweight manager for room shared files. Open it from
 * the room header and participants get a list of every file the
 * room has on its agents' ``memory/shared/``; deleting here also
 * pushes a delete frame to every placed agent so their copy goes
 * with it.
 *
 * #302 — data plane lives in ``useRoomFiles``. The right-rail
 * ``FilesSection`` consumes the same hook so the dialog and the
 * sidebar render the same list with one cache. The dialog is kept
 * around for the legacy entry point (#246 header button); ChatPage
 * may drop the entry point once the right rail ships.
 */
export default function RoomSharedFilesDialog({
  roomId,
  open,
  onOpenChange,
}: RoomSharedFilesDialogProps) {
  const { files, loading, error, refresh, remove } = useRoomFiles(roomId)

  // The hook auto-fetches on roomId change. We only want to *force* a
  // refresh when the dialog re-opens (the user may have edited files
  // elsewhere — e.g. via the right-rail FilesSection — between opens).
  useEffect(() => {
    if (open && roomId) void refresh()
  }, [open, roomId, refresh])

  const handleDelete = async (fileId: string) => {
    if (!roomId) return
    if (!confirm('이 파일을 룸에서 제거할까요? 참여 에이전트의 복사본도 함께 삭제됩니다.'))
      return
    await remove(fileId)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Paperclip className="h-4 w-4 text-[var(--color-foreground-muted)]" />
            공유 파일
          </DialogTitle>
          <DialogDescription>
            이 룸에 첨부된 파일은 참여 에이전트의 <code>memory/shared/</code>로 복사 배포됩니다.
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
        ) : files.length === 0 ? (
          <p className="text-sm text-[var(--color-foreground-subtle)]">
            아직 공유된 파일이 없습니다.
          </p>
        ) : (
          <ul className="flex flex-col divide-y divide-[var(--color-border)]">
            {files.map(f => (
              <li
                key={f.id}
                className="flex items-center gap-3 py-2"
              >
                <FileText className="h-4 w-4 shrink-0 text-[var(--color-foreground-subtle)]" />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm text-[var(--color-foreground)]" title={f.filename}>
                    {f.filename}
                  </p>
                  <p className="text-[11px] text-[var(--color-foreground-subtle)]">
                    {formatBytes(f.size_bytes)} · {f.mime}
                  </p>
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => handleDelete(f.id)}
                  aria-label={`Delete ${f.filename}`}
                  title="삭제"
                >
                  <Trash2 className="h-4 w-4 text-[var(--color-foreground-subtle)]" />
                </Button>
              </li>
            ))}
          </ul>
        )}
      </DialogContent>
    </Dialog>
  )
}

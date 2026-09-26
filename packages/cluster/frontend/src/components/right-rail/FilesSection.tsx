import { useRef, useState } from 'react'
import { FileText, Trash2, Upload } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useRoomFiles } from '@/hooks/useRoomFiles'
import { useLocale } from '@/i18n/LocaleProvider'
import { useFeedback } from '@/components/feedback/FeedbackProvider'

interface FilesSectionProps {
  roomId: string
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/**
 * Compact shared-files panel for the right rail (#302). Wraps the
 * existing #246 dialog data plane (``useRoomFiles``) and adds an
 * inline upload trigger so users no longer need to open the legacy
 * dialog for the common case.
 */
export default function FilesSection({ roomId }: FilesSectionProps) {
  const { t } = useLocale()
  const { confirm } = useFeedback()
  const { files, error, upload, remove } = useRoomFiles(roomId)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const [uploading, setUploading] = useState(false)

  const handlePick = () => fileInputRef.current?.click()

  const handleFiles = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (!f) return
    setUploading(true)
    await upload(f)
    setUploading(false)
    // Reset so the same filename can be re-picked later.
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  const handleDelete = async (fileId: string) => {
    if (!await confirm({
      title: t('guest.removeFileTitle'),
      description: t('guest.deleteFileConfirm'),
      confirmLabel: t('guest.removeFileTitle'),
      destructive: true,
    })) return
    await remove(fileId)
  }

  return (
    <section className="flex min-w-0 flex-col border-t border-[var(--color-border)]">
      <header className="flex items-baseline justify-between px-3 py-2">
        <h3 className="text-sm font-semibold text-[var(--color-foreground)]">
          {t('guest.sharedFiles')}
        </h3>
        <span className="text-[11px] text-[var(--color-foreground-subtle)]">
          {files.length}
        </span>
      </header>

      {error && (
        <p
          role="alert"
          className="px-3 py-1 text-[11px] text-[var(--color-destructive)]"
        >
          {error}
        </p>
      )}

      <div className="min-w-0 px-1">
        {files.length === 0 && (
          <div className="px-3 py-4 text-center text-[12px] text-[var(--color-foreground-subtle)]">
            {t('files.empty')}
          </div>
        )}
        {files.map((f) => (
          <div
            key={f.id}
            data-testid={`right-rail-file-row-${f.id}`}
            className="group relative flex min-w-0 items-center gap-2 rounded-[var(--radius-sm)] px-2 py-1.5 hover:bg-[var(--color-surface-alt)]"
          >
            <FileText className="h-3.5 w-3.5 shrink-0 text-[var(--color-foreground-subtle)]" />
            <div className="min-w-0 flex-1">
              <p
                className="truncate text-[13px] text-[var(--color-foreground)]"
                title={f.filename}
              >
                {f.filename}
              </p>
              <p className="truncate text-[10px] text-[var(--color-foreground-subtle)]">
                {formatBytes(f.size_bytes)} · {f.mime}
              </p>
            </div>
            {/* #325 — delete is absolute so the meta column reaches
                the row's inner right edge at rest, aligning with the
                section header's right-side counter. */}
            <button
              onClick={() => handleDelete(f.id)}
              className="absolute right-1 top-1/2 flex min-h-9 min-w-9 -translate-y-1/2 items-center justify-center rounded text-[var(--color-destructive)] opacity-100 transition-all hover:bg-[var(--color-danger-soft)] lg:right-2 lg:opacity-0 lg:group-hover:opacity-100 lg:group-focus-within:opacity-100"
              aria-label={t('guest.deleteFile', { name: f.filename })}
            >
              <Trash2 className="h-3 w-3" />
            </button>
          </div>
        ))}
      </div>

      <div className="border-t border-[var(--color-border)] px-3 py-2">
        <input
          ref={fileInputRef}
          type="file"
          className="hidden"
          onChange={handleFiles}
          aria-label={t('files.uploadToRoom')}
        />
        <Button
          variant="ghost"
          size="sm"
          onClick={handlePick}
          disabled={uploading}
          className="w-full justify-start"
        >
          <Upload className="h-3.5 w-3.5 mr-1.5" />
          <span className="text-[13px]">{uploading ? t('files.uploading') : t('files.upload')}</span>
        </Button>
      </div>
    </section>
  )
}

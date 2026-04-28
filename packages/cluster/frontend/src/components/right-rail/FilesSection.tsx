import { useRef, useState } from 'react'
import { FileText, Trash2, Upload } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useRoomFiles } from '@/hooks/useRoomFiles'

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

  const handleDelete = async (fileId: string, filename: string) => {
    if (!confirm(`'${filename}'을(를) 룸에서 제거할까요? 참여 에이전트의 복사본도 함께 삭제됩니다.`)) return
    await remove(fileId)
  }

  return (
    <section className="flex flex-col border-t border-[var(--color-border)]">
      <header className="flex items-baseline justify-between px-3 py-2">
        <h3 className="text-[11px] font-semibold uppercase tracking-wider text-[var(--color-foreground-subtle)]">
          Shared Files
        </h3>
        <span className="text-[11px] text-[var(--color-foreground-subtle)]">
          {files.length}
        </span>
      </header>

      {error && (
        <p
          role="alert"
          className="px-3 py-1 text-[11px] text-red-600"
        >
          {error}
        </p>
      )}

      <div className="px-1">
        {files.length === 0 && (
          <div className="px-3 py-4 text-center text-[12px] text-[var(--color-foreground-subtle)]">
            No files yet
          </div>
        )}
        {files.map((f) => (
          <div
            key={f.id}
            data-testid={`right-rail-file-row-${f.id}`}
            className="group flex items-center gap-2 rounded-[var(--radius-sm)] px-2 py-1.5 hover:bg-[var(--color-surface-alt)]"
          >
            <FileText className="h-3.5 w-3.5 shrink-0 text-[var(--color-foreground-subtle)]" />
            <div className="min-w-0 flex-1">
              <p
                className="truncate text-[13px] text-[var(--color-foreground)]"
                title={f.filename}
              >
                {f.filename}
              </p>
              <p className="text-[10px] text-[var(--color-foreground-subtle)]">
                {formatBytes(f.size_bytes)} · {f.mime}
              </p>
            </div>
            <button
              onClick={() => handleDelete(f.id, f.filename)}
              className="opacity-0 group-hover:opacity-100 p-0.5 rounded hover:bg-red-50 text-red-400 hover:text-red-600 transition-all shrink-0"
              aria-label={`Delete ${f.filename}`}
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
          aria-label="Upload file to room"
        />
        <Button
          variant="ghost"
          size="sm"
          onClick={handlePick}
          disabled={uploading}
          className="w-full justify-start"
        >
          <Upload className="h-3.5 w-3.5 mr-1.5" />
          <span className="text-[13px]">{uploading ? 'Uploading…' : 'Upload file'}</span>
        </Button>
      </div>
    </section>
  )
}

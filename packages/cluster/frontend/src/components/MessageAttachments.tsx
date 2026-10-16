import { useState } from 'react'
import { AlertTriangle, Download, FileText, Image as ImageIcon, ImageOff } from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { useArtifactBlobUrl } from '@/components/ArtifactImage'
import { useFeedback } from '@/components/feedback/FeedbackProvider'
import { useLocale } from '@/i18n/LocaleProvider'
import type { MessageKey } from '@/i18n/messages'
import {
  extractArtifactReferences,
  extractUndeliveredAttachments,
  type ArtifactReference,
  type UndeliveredAttachmentReason,
} from '@/lib/messageAttachments'
import {
  fetchArtifactBlobUrl,
  formatBytes,
  isImageMime,
  startArtifactDownload,
} from '@/lib/roomArtifacts'

const REASON_KEYS: Record<UndeliveredAttachmentReason, MessageKey> = {
  too_large: 'chat.attachmentReasonTooLarge',
  unsupported_type: 'chat.attachmentReasonUnsupportedType',
  too_many: 'chat.attachmentReasonTooMany',
  upload_failed: 'chat.attachmentReasonUploadFailed',
}

function useArtifactDownload() {
  const { t } = useLocale()
  const { notify } = useFeedback()
  const [pending, setPending] = useState(false)
  const download = async (ref: ArtifactReference) => {
    if (pending) return
    setPending(true)
    try {
      const blob = await fetchArtifactBlobUrl(ref.roomId, ref.id)
      startArtifactDownload(blob, blob.filename || ref.name)
    } catch {
      notify({ message: t('chat.attachmentDownloadFailed', { name: ref.name }), tone: 'error' })
    } finally {
      setPending(false)
    }
  }
  return { download, pending }
}

function DownloadButton({ attachment }: { attachment: ArtifactReference }) {
  const { t } = useLocale()
  const { download, pending } = useArtifactDownload()
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      aria-label={t('chat.attachmentDownload', { name: attachment.name })}
      title={t('chat.attachmentDownload', { name: attachment.name })}
      aria-busy={pending || undefined}
      disabled={pending}
      onClick={() => void download(attachment)}
    >
      <Download className="h-4 w-4 text-[var(--color-foreground-muted)]" aria-hidden="true" />
    </Button>
  )
}

function FileCard({ attachment, unavailable = false }: { attachment: ArtifactReference; unavailable?: boolean }) {
  const { t } = useLocale()
  const Icon = unavailable ? ImageOff : FileText
  return (
    <div
      data-testid="message-attachment-file"
      className="flex w-full max-w-sm items-center gap-3 rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-[var(--color-surface)] py-1.5 pl-3 pr-1.5"
    >
      <Icon className="h-5 w-5 shrink-0 text-[var(--color-foreground-subtle)]" aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <p className="truncate text-caption text-[var(--color-foreground)]" title={attachment.name}>
          {attachment.name}
        </p>
        <p className="text-badge text-[var(--color-foreground-muted)]">
          {unavailable ? t('chat.attachmentUnavailable') : formatBytes(attachment.sizeBytes)}
        </p>
      </div>
      {!unavailable && <DownloadButton attachment={attachment} />}
    </div>
  )
}

function ImageAttachment({ attachment }: { attachment: ArtifactReference }) {
  const { t } = useLocale()
  const image = useArtifactBlobUrl(attachment.roomId, attachment.id)
  const [open, setOpen] = useState(false)

  if (image.status === 'failed') return <FileCard attachment={attachment} unavailable />
  if (image.status === 'loading') {
    return (
      <div
        className="flex h-40 w-full max-w-xs items-center justify-center rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-[var(--color-surface-alt)]"
        aria-busy="true"
      >
        <ImageIcon className="h-6 w-6 text-[var(--color-foreground-subtle)]" aria-hidden="true" />
      </div>
    )
  }
  return (
    <>
      <button
        type="button"
        aria-label={t('chat.attachmentOpen', { name: attachment.name })}
        onClick={() => setOpen(true)}
        className="block max-w-full cursor-zoom-in overflow-hidden rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-[var(--color-surface-alt)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-brand-focus)] sm:max-w-sm"
      >
        <img
          src={image.src}
          alt={attachment.name}
          loading="lazy"
          decoding="async"
          className="block max-h-64 max-w-full object-contain"
        />
      </button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-[min(92vw,64rem)]">
          <DialogHeader>
            <DialogTitle className="truncate">{attachment.name}</DialogTitle>
            <DialogDescription>
              {formatBytes(attachment.sizeBytes)} · {attachment.mime}
            </DialogDescription>
          </DialogHeader>
          <div className="flex max-h-[70vh] justify-center overflow-auto rounded-[var(--radius-sm)] bg-[var(--color-surface-alt)]">
            <img src={image.src} alt={attachment.name} className="max-h-[70vh] max-w-full object-contain" />
          </div>
          <DialogFooter>
            <DialogDownloadButton attachment={attachment} />
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

function DialogDownloadButton({ attachment }: { attachment: ArtifactReference }) {
  const { t } = useLocale()
  const { download, pending } = useArtifactDownload()
  return (
    <Button
      type="button"
      variant="outline"
      aria-label={t('chat.attachmentDownload', { name: attachment.name })}
      aria-busy={pending || undefined}
      disabled={pending}
      onClick={() => void download(attachment)}
    >
      <Download className="h-4 w-4" aria-hidden="true" />
      {t('chat.attachmentDownloadAction')}
    </Button>
  )
}

/** #854 — files an agent attached to its reply: images inline (click to
 * enlarge), other types as download cards, plus a note for each file the
 * agent wrote but could not attach. #873 — also a person's own images,
 * aligned to the end of their right-hand bubble. */
export default function MessageAttachments({
  metadata,
  align = 'start',
}: {
  metadata?: Record<string, unknown>
  align?: 'start' | 'end'
}) {
  const { t } = useLocale()
  const attachments = extractArtifactReferences(metadata)
  const undelivered = extractUndeliveredAttachments(metadata)
  if (attachments.length === 0 && undelivered.length === 0) return null
  return (
    <div
      className={`mt-2 flex flex-col gap-2 ${align === 'end' ? 'items-end' : 'items-start'}`}
      data-testid="message-attachments"
    >
      {attachments.map(attachment =>
        isImageMime(attachment.mime)
          ? <ImageAttachment key={attachment.id} attachment={attachment} />
          : <FileCard key={attachment.id} attachment={attachment} />,
      )}
      {undelivered.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {undelivered.map((item, index) => (
            <span
              key={`${item.name}-${index}`}
              data-testid="undelivered-attachment"
              title={t('chat.attachmentUndeliveredTitle')}
              className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-[var(--color-border)] bg-[var(--color-surface-alt)] px-2 py-0.5 text-xs text-[var(--color-foreground-muted)]"
            >
              <AlertTriangle className="h-3 w-3 shrink-0 text-[var(--color-warning)]" aria-hidden="true" />
              <span className="min-w-0 break-words">
                {t('chat.attachmentUndelivered', { name: item.name, reason: t(REASON_KEYS[item.reason]) })}
              </span>
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

import { ShieldAlert } from 'lucide-react'

import type { WorkspaceAttachmentSummary } from '@/hooks/useRooms'

interface WorkspaceAttachmentBannerProps {
  attachments: WorkspaceAttachmentSummary[]
}

export default function WorkspaceAttachmentBanner({
  attachments,
}: WorkspaceAttachmentBannerProps) {
  if (attachments.length === 0) return null

  const writeEnabled = attachments.some((attachment) => attachment.mode === 'write')
  const labels = attachments.map((attachment) => attachment.label).join(', ')

  return (
    <div
      role="status"
      aria-label="External workspace attachment active"
      className="flex items-start gap-2 border-b border-amber-300 bg-amber-50 px-4 py-2 text-xs text-amber-950"
    >
      <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
      <div className="min-w-0">
        <span className="font-semibold">External workspace attached</span>
        <span className="ml-2 break-words">
          {labels} · {writeEnabled ? 'scoped write' : 'read-only'} · audited
        </span>
      </div>
    </div>
  )
}

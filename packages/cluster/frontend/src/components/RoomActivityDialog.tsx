import { useEffect, useState } from 'react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { apiFetch } from '@/lib/api'
import {
  formatDuration,
  splitLogs,
  turnDotClass,
  turnLabel,
  type ActivityLog,
} from '@/components/agent-settings/ActivityPanel'
import { useLocale } from '@/i18n/LocaleProvider'

interface RoomActivityDialogProps {
  roomId: string | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

// #429 — admin-only room flow view. Reuses ActivityPanel's turn grouping
// (splitLogs by request_id) but over EVERY agent in the room, so the
// multi-agent flow reads as one chronological list. Backed by the #427
// GET /api/v1/rooms/{id}/activity endpoint. One fetch on open (no polling
// — matches the per-agent ActivityPanel).
export default function RoomActivityDialog({
  roomId,
  open,
  onOpenChange,
}: RoomActivityDialogProps) {
  const { t, formatDate } = useLocale()
  const [logs, setLogs] = useState<ActivityLog[]>([])
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    if (!open || !roomId) return
    let cancelled = false
    setLoaded(false)
    apiFetch(`/api/v1/rooms/${roomId}/activity?limit=200`).then(async r => {
      if (cancelled || !r.ok) {
        if (!cancelled) setLoaded(true)
        return
      }
      setLogs(await r.json())
      setLoaded(true)
    })
    return () => {
      cancelled = true
    }
  }, [open, roomId])

  const { turns } = splitLogs(logs)
  // #431 — resolve each turn's parent (the agent turn that triggered it)
  // so the flow reads as A→B. Only turns whose parent is in this window
  // get the "↳" marker; an off-window parent degrades to no marker.
  const turnById = new Map(turns.map(t => [t.requestId, t]))
  const localizedOutcome = (outcome: string) => {
    const keys = {
      responded: 'rooms.outcomeResponded',
      'no response': 'rooms.outcomeSilent',
      orphaned: 'rooms.outcomeOrphaned',
      'in flight': 'rooms.outcomeInFlight',
      failed: 'rooms.outcomeFailed',
      timeout: 'rooms.outcomeTimeout',
      cancelled: 'rooms.outcomeCancelled',
      rejected: 'rooms.outcomeRejected',
    } as const
    return outcome in keys ? t(keys[outcome as keyof typeof keys]) : outcome
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t('rooms.activity')}</DialogTitle>
          <DialogDescription>
            {t('rooms.activityDescription')}
          </DialogDescription>
        </DialogHeader>

        <div className="max-h-[60vh] overflow-y-auto space-y-1 py-1">
          {!loaded && (
            <p className="text-caption text-[var(--color-foreground-muted)]">
              {t('common.loading')}
            </p>
          )}
          {loaded && turns.length === 0 && (
            <p className="text-caption text-[var(--color-foreground-muted)]">
              {t('rooms.noActivity')}
            </p>
          )}
          {turns.map(turn => {
            const parent = turn.parentRequestId
              ? turnById.get(turn.parentRequestId)
              : undefined
            const parentLabel = parent
              ? parent.agentId
                ? parent.agentId.slice(0, 6)
                : t('rooms.agent')
              : null
            return (
            <div
              key={turn.requestId}
              className="flex items-center gap-2 rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1.5 text-xs"
              data-testid="room-activity-turn"
            >
              <span
                className={`inline-block h-1.5 w-1.5 rounded-full shrink-0 ${turnDotClass(turn)}`}
                aria-label={localizedOutcome(turnLabel(turn))}
              />
              {parentLabel && (
                <span
                  className="text-[10px] text-[var(--color-foreground-subtle)] shrink-0"
                  title={t('rooms.triggeredBy', { agent: parent?.agentId ?? t('rooms.agent'), request: turn.parentRequestId ?? '' })}
                  data-testid="room-activity-parent"
                >
                  ↳ {t('rooms.from')} <span className="font-mono">{parentLabel}</span>
                </span>
              )}
              <span
                className="font-mono text-[10px] text-[var(--color-foreground-subtle)] shrink-0"
                title={turn.agentId ?? undefined}
              >
                {turn.agentId ? turn.agentId.slice(0, 6) : t('rooms.agent')}
              </span>
              <span className="font-medium text-[var(--color-foreground)]">
                {formatDate(new Date(turn.firstTs), { hour: 'numeric', minute: '2-digit', second: '2-digit' })}
              </span>
              <span className="text-[var(--color-foreground-muted)]">
                · {formatDuration(turn.durationMs ?? turn.lastTs - turn.firstTs)}
              </span>
              {turn.engine && (
                <span className="text-[var(--color-foreground-muted)]">
                  · {turn.engine}
                </span>
              )}
              <span className="text-[var(--color-foreground-muted)]">
                · {localizedOutcome(turnLabel(turn))}
              </span>
              {turn.error && (
                <span
                  className="ml-auto truncate text-[10px] text-[var(--color-destructive,#d74c4c)]"
                  title={turn.error}
                >
                  {turn.error}
                </span>
              )}
            </div>
            )
          })}
        </div>
      </DialogContent>
    </Dialog>
  )
}

import { useMemo } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import MarkdownContent from '@/components/MarkdownContent'
import { useLocale } from '@/i18n/LocaleProvider'
import { uuid, type SharedDelegation, type SharedTarget } from '@/lib/federationApi'
import { delegationStateCopy, executionFailureCopy } from '@/lib/sharedRoomCopy'
import type { SharedRoomState } from '@/hooks/useSharedRoom'

export default function DelegationCard({ delegation, target, room }: {
  delegation: SharedDelegation; target?: SharedTarget; room: SharedRoomState
}) {
  const { t } = useLocale()
  // An ambiguous network failure resends the same cancellation, while a newer
  // authority revision requires a new explicit action from the user.
  const requestId = useMemo(uuid, [delegation.delegation_id, delegation.revision])
  const pendingCancel = room.data?.submissions.some(item => item.kind === 'task.cancel' && item.delegation_id === delegation.delegation_id && (item.state === 'unconfirmed' || (item.receipt && item.receipt.revision > delegation.revision)))
  const stopping = delegation.state === 'cancel_requested' || pendingCancel
  return (
    <section aria-label={t('federation.delegations')} className="mt-3 min-w-0 space-y-3 rounded-[var(--radius-md)] border bg-[var(--color-surface-alt)] p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 break-words text-sm">
          <p className="font-medium">{target?.name || t('federation.room.unknownAgent')}</p>
          <p className="text-caption text-[var(--color-foreground-muted)]">{target?.is_local ? t('federation.room.currentServer') : target?.node_name || (target?.server_label ? t('federation.room.serverAddress', { address: target.server_label }) : t('federation.room.unknownServer'))}</p>
        </div>
        <Badge role="status" aria-live="polite" variant={['failed', 'rejected'].includes(delegation.state) ? 'destructive' : 'outline'}>
          {t(delegationStateCopy(delegation.state))}
        </Badge>
      </div>
      {stopping && <p className="text-sm text-[var(--color-foreground-muted)]">{t('federation.room.stopExplanation')}</p>}
      {delegation.state === 'unknown' && <p className="text-sm text-[var(--color-foreground-muted)]">{t('federation.room.unknownExplanation')}</p>}
      {delegation.result_markdown && (
        <div className="min-w-0 overflow-hidden break-words text-sm">
          <p className="mb-2 font-medium">{t('federation.room.result')}</p>
          <MarkdownContent content={delegation.result_markdown} />
        </div>
      )}
      {delegation.state === 'completed' && !delegation.result_markdown && <p className="text-sm">{t('federation.room.resultUnavailable')}</p>}
      {delegation.state === 'failed' && <p className="text-sm text-[var(--color-danger)]">{t(executionFailureCopy(delegation.error))}</p>}
      {delegation.can_cancel && !stopping && (
        <Button size="sm" variant="outline" disabled={room.busy || Boolean(room.error)}
          onClick={() => void room.cancel(delegation.delegation_id, { request_id: requestId, expected_revision: delegation.revision })}>
          {t('federation.room.stop')}
        </Button>
      )}
    </section>
  )
}

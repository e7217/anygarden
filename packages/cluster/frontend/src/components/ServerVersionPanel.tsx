import { ArrowUpCircle, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useFeedback } from '@/components/feedback/FeedbackProvider'
import type { NodeUpdateControl, PackageUpdate, UpdateStatus } from '@/hooks/useSystemVersion'
import { parseServerDate } from '@/lib/datetime'
import { useLocale } from '@/i18n/LocaleProvider'
import type { MessageKey } from '@/i18n/messages'

/**
 * "This server" panel on the Machines page (#546, #888) — shows each
 * tracked package's running version vs the latest on PyPI, with a manual
 * "check for updates" action. An integrated node can also install the update
 * and restart itself in place (#927); otherwise the panel shows the command.
 */
export default function ServerVersionPanel({
  status,
  nodeUpdate,
}: {
  status: UpdateStatus
  nodeUpdate?: NodeUpdateControl
}) {
  const { t } = useLocale()
  const { updates, loading, error, refresh } = status
  const checkError = error?.match(/^Check failed \((\d+)\)$/)
  const displayError = checkError
    ? t('admin.system.checkRequestFailed', { status: checkError[1] })
    : error === 'Check failed — network error'
      ? t('admin.system.networkError')
      : error

  return (
    <section aria-labelledby="server-version-heading" className="flex max-w-2xl flex-col gap-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h2 id="server-version-heading" className="text-lead text-[var(--color-foreground)]">{t('admin.machines.serverEntry')}</h2>
          <p className="mt-1 text-sm text-[var(--color-foreground-muted)]">
            {t('admin.system.description')}
          </p>
        </div>
        <Button onClick={refresh} disabled={loading} className="w-full sm:w-auto sm:shrink-0">
          <RefreshCw className={loading ? 'animate-spin' : ''} />
          {loading ? t('admin.system.checking') : t('admin.system.checkUpdates')}
        </Button>
      </div>

      {error && (
        <p className="text-sm text-[var(--color-danger)]">{displayError}</p>
      )}

      <div className="flex flex-col gap-3">
        {updates.map((u) => (
          <PackageRow
            key={u.package}
            update={u}
            nodeUpdate={u.package === 'anygarden' ? nodeUpdate : undefined}
          />
        ))}
        {updates.length === 0 && (
          <p className="text-sm text-[var(--color-foreground-subtle)]">
            {t('admin.system.noPackages')}
          </p>
        )}
      </div>
    </section>
  )
}

function PackageRow({ update, nodeUpdate }: { update: PackageUpdate; nodeUpdate?: NodeUpdateControl }) {
  const { t, formatDate } = useLocale()
  const { package: pkg, current, latest, update_available, checked_at, error } = update
  const canUpdateInPlace = !!nodeUpdate?.state?.supported
  const unsupportedReason = nodeUpdate?.state && !nodeUpdate.state.supported ? nodeUpdate.state.reason : null

  return (
    <div className="rounded-[12px] border border-[var(--color-border)] bg-[var(--color-surface-elevated)] p-[var(--space-4)] shadow-whisper">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="font-medium text-[var(--color-foreground)]">{pkg}</p>
          <p className="text-sm text-[var(--color-foreground-muted)]">
            {t('admin.system.current')} <span className="font-mono">{current}</span>
            {latest && (
              <>
                {' · '}{t('admin.system.latest')} <span className="font-mono">{latest}</span>
              </>
            )}
          </p>
        </div>
        <StatusBadge
          available={update_available}
          // #773 — the server already compared versions; a different PyPI
          // version with no update means this build is ahead of the release.
          ahead={!update_available && !!latest && latest !== current}
          error={error}
          checked={!!checked_at}
        />
      </div>

      {update_available && latest && canUpdateInPlace && nodeUpdate && (
        <NodeUpdateAction control={nodeUpdate} latest={latest} />
      )}

      {update_available && !canUpdateInPlace && (
        <div className="mt-3 rounded-[var(--radius-sm)] bg-[var(--color-surface-alt)] p-2">
          <p className="text-xs text-[var(--color-foreground-muted)]">{t('admin.system.updateCommand')}</p>
          <code className="mt-0.5 block font-mono text-xs text-[var(--color-foreground)]">
            {`pip install -U ${pkg}`}
          </code>
          {unsupportedReason && (
            <p className="mt-1 text-xs text-[var(--color-foreground-muted)]">
              {t(UNSUPPORTED_REASON_KEYS[unsupportedReason] ?? 'admin.system.nodeUpdate.unsupported.unsupported_install')}
            </p>
          )}
        </div>
      )}

      {nodeUpdate && !(update_available && canUpdateInPlace) && <NodeUpdateOutcome control={nodeUpdate} />}

      {checked_at && (
        <p className="mt-2 text-badge font-normal text-[var(--color-foreground-subtle)]">
          {t('admin.system.lastChecked', { date: formatDate(parseServerDate(checked_at), { dateStyle: 'medium', timeStyle: 'short' }) })}
        </p>
      )}
    </div>
  )
}

function StatusBadge({
  available,
  ahead,
  error,
  checked,
}: {
  available: boolean
  ahead: boolean
  error: string | null
  checked: boolean
}) {
  const { t } = useLocale()
  if (error) {
    return (
      <span className="shrink-0 rounded-full bg-[var(--color-surface-alt)] px-2 py-0.5 text-badge font-semibold text-[var(--color-foreground-muted)]">
        {t('admin.system.checkFailed')}
      </span>
    )
  }
  if (!checked) {
    return (
      <span className="shrink-0 rounded-full bg-[var(--color-surface-alt)] px-2 py-0.5 text-badge font-semibold text-[var(--color-foreground-muted)]">
        {t('admin.system.notChecked')}
      </span>
    )
  }
  if (available) {
    return (
      <span className="shrink-0 rounded-full bg-[color:color-mix(in_srgb,var(--color-brand)_15%,transparent)] px-2 py-0.5 text-badge font-semibold text-[var(--color-brand-text)]">
        {t('admin.system.updateAvailable')}
      </span>
    )
  }
  return (
    <span className="shrink-0 rounded-full bg-[var(--color-surface-alt)] px-2 py-0.5 text-badge font-semibold text-[var(--color-foreground-muted)]">
      {ahead ? t('admin.system.aheadOfPypi') : t('admin.system.upToDate')}
    </span>
  )
}

// Why this server cannot update itself (``GET /node-update`` reason).
const UNSUPPORTED_REASON_KEYS: Record<string, MessageKey> = {
  not_integrated_node: 'admin.system.nodeUpdate.unsupported.not_integrated_node',
  unsupported_platform: 'admin.system.nodeUpdate.unsupported.unsupported_platform',
  development_install: 'admin.system.nodeUpdate.unsupported.development_install',
  unsupported_install: 'admin.system.nodeUpdate.unsupported.unsupported_install',
}

/** Install-and-restart for an integrated node (#927). */
function NodeUpdateAction({ control, latest }: { control: NodeUpdateControl; latest: string }) {
  const { t } = useLocale()
  const { confirm } = useFeedback()
  const busy = control.progress.phase === 'installing' || control.progress.phase === 'restarting'

  const onUpdate = async () => {
    const ok = await confirm({
      title: t('admin.system.nodeUpdate.confirmTitle'),
      description: t('admin.system.nodeUpdate.confirmDescription', { version: latest }),
      confirmLabel: t('admin.system.nodeUpdate.confirmAction'),
    })
    if (ok) await control.start(latest)
  }

  // Once the node is back on the new version only the reload remains to do.
  const done = control.progress.phase === 'updated' && !control.startError

  return (
    <div className="mt-3 flex flex-col gap-2">
      {!done && (
        <>
          <p className="text-sm text-[var(--color-foreground-muted)]">{t('admin.system.nodeUpdate.hint')}</p>
          <div>
            <Button size="sm" onClick={onUpdate} disabled={busy}>
              <ArrowUpCircle />
              {t('admin.system.nodeUpdate.action', { version: latest })}
            </Button>
          </div>
        </>
      )}
      <NodeUpdateOutcome control={control} />
    </div>
  )
}

/** Progress and result of a node update; announced to screen readers. */
function NodeUpdateOutcome({ control }: { control: NodeUpdateControl }) {
  const { t } = useLocale()
  const { progress, startError } = control
  let message: string | null = null
  let tone = 'text-[var(--color-foreground-muted)]'
  if (startError) {
    message = startError === 'network'
      ? t('admin.system.networkError')
      : t('admin.system.nodeUpdate.failed', { error: startError })
    tone = 'text-[var(--color-danger)]'
  } else {
    switch (progress.phase) {
      case 'installing':
        message = t('admin.system.nodeUpdate.installing', { version: progress.targetVersion ?? '' })
        break
      case 'restarting':
        message = t('admin.system.nodeUpdate.restarting')
        break
      case 'failed':
        message = t('admin.system.nodeUpdate.failed', { error: progress.error ?? '' })
        tone = 'text-[var(--color-danger)]'
        break
      case 'same_version':
        message = t('admin.system.nodeUpdate.sameVersion', { version: progress.version ?? '' })
        tone = 'text-[var(--color-danger)]'
        break
      case 'stalled':
        message = t('admin.system.nodeUpdate.stalled')
        tone = 'text-[var(--color-warning)]'
        break
      case 'updated':
        message = t('admin.system.nodeUpdate.updated', { version: progress.version ?? '' })
        tone = 'text-[var(--color-success)]'
        break
    }
  }
  return (
    <div role="status" aria-live="polite" className="flex flex-wrap items-center gap-2">
      {message && <p className={`text-sm ${tone}`}>{message}</p>}
      {progress.phase === 'updated' && !startError && (
        // The page still runs the previous build's scripts.
        <Button size="sm" variant="outline" onClick={() => window.location.reload()}>
          {t('admin.system.nodeUpdate.reload')}
        </Button>
      )}
    </div>
  )
}

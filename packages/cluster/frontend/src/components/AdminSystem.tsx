import { RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useUpdateStatus, type PackageUpdate } from '@/hooks/useSystemVersion'
import { parseServerDate } from '@/lib/datetime'
import { useLocale } from '@/i18n/LocaleProvider'

/**
 * Admin System panel (#546) — shows each tracked package's running
 * version vs the latest on PyPI, with a manual "check for updates"
 * action. Applying an update is intentionally out-of-band: the panel
 * surfaces the exact command rather than running it.
 */
export default function AdminSystem() {
  const { t } = useLocale()
  const { updates, loading, error, refresh } = useUpdateStatus(true)
  const checkError = error?.match(/^Check failed \((\d+)\)$/)
  const displayError = checkError
    ? t('admin.system.checkRequestFailed', { status: checkError[1] })
    : error === 'Check failed — network error'
      ? t('admin.system.networkError')
      : error

  return (
    <div className="mx-auto flex w-full max-w-4xl flex-col gap-6 px-4 py-5 sm:px-6 sm:py-6">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <h1 className="text-heading text-[var(--color-foreground)]">{t('admin.system.title')}</h1>
          <p className="mt-1 text-sm text-[var(--color-foreground-muted)]">
            {t('admin.system.description')}
          </p>
        </div>
        <Button onClick={refresh} disabled={loading} className="min-h-11 w-full sm:w-auto lg:shrink-0">
          <RefreshCw className={loading ? 'animate-spin' : ''} />
          {loading ? t('admin.system.checking') : t('admin.system.checkUpdates')}
        </Button>
      </div>

      {error && (
        <p className="text-sm text-[var(--color-danger,#d4442e)]">{displayError}</p>
      )}

      <div className="flex flex-col gap-3">
        {updates.map((u) => (
          <PackageRow key={u.package} update={u} />
        ))}
        {updates.length === 0 && (
          <p className="text-sm text-[var(--color-foreground-subtle)]">
            {t('admin.system.noPackages')}
          </p>
        )}
      </div>
    </div>
  )
}

function PackageRow({ update }: { update: PackageUpdate }) {
  const { t, formatDate } = useLocale()
  const { package: pkg, current, latest, update_available, checked_at, error } = update

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
        <StatusBadge available={update_available} error={error} checked={!!checked_at} />
      </div>

      {update_available && (
        <div className="mt-3 rounded-[var(--radius-sm)] bg-[var(--color-surface-alt)] p-2">
          <p className="text-xs text-[var(--color-foreground-muted)]">{t('admin.system.updateCommand')}</p>
          <code className="mt-0.5 block font-mono text-xs text-[var(--color-foreground)]">
            {pkg === 'anygarden-machine'
              ? `pip install -U ${pkg} && systemctl --user restart anygarden-machine`
              : `pip install -U ${pkg}`}
          </code>
        </div>
      )}

      {checked_at && (
        <p className="mt-2 text-[11px] text-[var(--color-foreground-subtle)]">
          {t('admin.system.lastChecked', { date: formatDate(parseServerDate(checked_at), { dateStyle: 'medium', timeStyle: 'short' }) })}
        </p>
      )}
    </div>
  )
}

function StatusBadge({
  available,
  error,
  checked,
}: {
  available: boolean
  error: string | null
  checked: boolean
}) {
  const { t } = useLocale()
  if (error) {
    return (
      <span className="shrink-0 rounded-full bg-[var(--color-surface-alt)] px-2 py-0.5 text-[11px] font-semibold text-[var(--color-foreground-muted)]">
        {t('admin.system.checkFailed')}
      </span>
    )
  }
  if (!checked) {
    return (
      <span className="shrink-0 rounded-full bg-[var(--color-surface-alt)] px-2 py-0.5 text-[11px] font-semibold text-[var(--color-foreground-muted)]">
        {t('admin.system.notChecked')}
      </span>
    )
  }
  if (available) {
    return (
      <span className="shrink-0 rounded-full bg-[color:color-mix(in_srgb,var(--color-brand)_15%,transparent)] px-2 py-0.5 text-[11px] font-semibold text-[var(--color-brand-text)]">
        {t('admin.system.updateAvailable')}
      </span>
    )
  }
  return (
    <span className="shrink-0 rounded-full bg-[var(--color-surface-alt)] px-2 py-0.5 text-[11px] font-semibold text-[var(--color-foreground-muted)]">
      {t('admin.system.upToDate')}
    </span>
  )
}

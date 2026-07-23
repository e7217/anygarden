import { RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useUpdateStatus, type PackageUpdate } from '@/hooks/useSystemVersion'
import { parseServerDate } from '@/lib/datetime'

/**
 * Admin System panel (#546) — shows each tracked package's running
 * version vs the latest on PyPI, with a manual "check for updates"
 * action. Applying an update is intentionally out-of-band: the panel
 * surfaces the exact command rather than running it.
 */
export default function AdminSystem() {
  const { updates, loading, error, refresh } = useUpdateStatus(true)

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4 p-[var(--space-6)]">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold text-[var(--color-foreground)]">System</h1>
          <p className="mt-1 text-sm text-[var(--color-foreground-muted)]">
            Running versions and available updates. Checking queries PyPI directly.
          </p>
        </div>
        <Button onClick={refresh} disabled={loading}>
          <RefreshCw className={loading ? 'animate-spin' : ''} />
          {loading ? 'Checking…' : 'Check for updates'}
        </Button>
      </div>

      {error && (
        <p className="text-sm text-[var(--color-danger,#d4442e)]">{error}</p>
      )}

      <div className="flex flex-col gap-3">
        {updates.map((u) => (
          <PackageRow key={u.package} update={u} />
        ))}
        {updates.length === 0 && (
          <p className="text-sm text-[var(--color-foreground-subtle)]">
            No packages tracked.
          </p>
        )}
      </div>
    </div>
  )
}

function PackageRow({ update }: { update: PackageUpdate }) {
  const { package: pkg, current, latest, update_available, checked_at, error } = update

  return (
    <div className="rounded-[12px] border border-[rgba(0,0,0,0.1)] bg-white p-[var(--space-4)] shadow-whisper">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="font-medium text-[var(--color-foreground)]">{pkg}</p>
          <p className="text-sm text-[var(--color-foreground-muted)]">
            current <span className="font-mono">{current}</span>
            {latest && (
              <>
                {' · '}latest <span className="font-mono">{latest}</span>
              </>
            )}
          </p>
        </div>
        <StatusBadge available={update_available} error={error} checked={!!checked_at} />
      </div>

      {update_available && (
        <div className="mt-3 rounded-[var(--radius-sm)] bg-[rgba(0,0,0,0.03)] p-2">
          <p className="text-xs text-[var(--color-foreground-muted)]">To update, run:</p>
          <code className="mt-0.5 block font-mono text-xs text-[var(--color-foreground)]">
            {pkg === 'anygarden-machine'
              ? `pip install -U ${pkg} && systemctl --user restart anygarden-machine`
              : `pip install -U ${pkg}`}
          </code>
        </div>
      )}

      {checked_at && (
        <p className="mt-2 text-[11px] text-[var(--color-foreground-subtle)]">
          Last checked {parseServerDate(checked_at).toLocaleString()}
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
  if (error) {
    return (
      <span className="shrink-0 rounded-full bg-[rgba(0,0,0,0.05)] px-2 py-0.5 text-[11px] font-semibold text-[var(--color-foreground-muted)]">
        check failed
      </span>
    )
  }
  if (!checked) {
    return (
      <span className="shrink-0 rounded-full bg-[rgba(0,0,0,0.05)] px-2 py-0.5 text-[11px] font-semibold text-[var(--color-foreground-muted)]">
        not checked
      </span>
    )
  }
  if (available) {
    return (
      <span className="shrink-0 rounded-full bg-[#f2f9ff] px-2 py-0.5 text-[11px] font-semibold text-[#097fe8]">
        update available
      </span>
    )
  }
  return (
    <span className="shrink-0 rounded-full bg-[rgba(0,0,0,0.05)] px-2 py-0.5 text-[11px] font-semibold text-[var(--color-foreground-muted)]">
      up to date
    </span>
  )
}

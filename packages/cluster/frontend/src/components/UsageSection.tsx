import { useState } from 'react'
import { BarChart3, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useUsage, type UsageBucket } from '@/hooks/useUsage'
import { cn } from '@/lib/utils'
import { useLocale } from '@/i18n/LocaleProvider'

/**
 * Usage aggregates — rendered as a 3-card summary + horizontal bars
 * for the top models + a plain list for the top agents.
 */

const WINDOWS = [
  { value: '1h', label: 'admin.usage.oneHour' },
  { value: '24h', label: 'admin.usage.twentyFourHours' },
  { value: '7d', label: 'admin.usage.sevenDays' },
  { value: '30d', label: 'admin.usage.thirtyDays' },
] as const

export function UsageSection() {
  const { t } = useLocale()
  const [window, setWindow] = useState('24h')
  const { usage, status, error, refresh } = useUsage(window)
  const requestError = error?.match(/^Failed to load usage \((\d+)\)$/)
  const displayError = requestError
    ? t('admin.usage.requestFailed', { status: requestError[1] })
    : error

  const totalTokens = usage
    ? usage.by_model.reduce(
      (sum, b) => sum + b.prompt_tokens + b.completion_tokens, 0,
    )
    : 0

  return (
    <div className="mx-auto w-full max-w-4xl px-4 py-5 sm:px-6 sm:py-6">
      <header className="mb-6 flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <h1 className="text-heading text-[var(--color-foreground)]">
            {t('admin.usage.title')}
          </h1>
          <p className="mt-1 text-[13px] text-[var(--color-foreground-muted)]">
            {t('admin.usage.description')}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 lg:shrink-0">
          <select
            aria-label={t('admin.usage.period')}
            value={window}
            onChange={e => setWindow(e.target.value)}
            className="h-9 rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-2.5 text-[13px]"
          >
            {WINDOWS.map(w => (
              <option key={w.value} value={w.value}>{t(w.label)}</option>
            ))}
          </select>
          <Button variant="ghost" size="sm" onClick={refresh}>
            <RefreshCw className={cn('mr-1 h-3.5 w-3.5', status === 'loading' && 'animate-spin')} />
            {t('common.refresh')}
          </Button>
        </div>
      </header>

      {status === 'error' && (
        <div className="mb-4 rounded-[var(--radius-md)] border border-[var(--color-destructive)]/30 bg-[var(--color-destructive)]/10 px-3 py-2 text-[13px] text-[var(--color-destructive)]">
          {t('admin.usage.loadError', { error: displayError ?? '' })}
        </div>
      )}

      {/* Summary cards */}
      <div className="mb-6 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <SummaryCard
          value={formatCount(usage?.total_requests ?? 0)}
          label={t('admin.usage.requests')}
        />
        <SummaryCard
          value={formatTokens(totalTokens)}
          label={t('admin.usage.tokensWindow', { hours: usage?.window_hours ?? 0 })}
        />
        <SummaryCard
          value={usage ? `$${usage.total_cost_usd.toFixed(4)}` : "$—"}
          label={t('admin.usage.reportedCost')}
          hint={t('admin.usage.costHint')}
        />
      </div>

      {/* By model */}
      <section className="mb-6">
        <h2 className="mb-2 text-[14px] font-semibold text-[var(--color-foreground)]">
          {t('admin.usage.byModel')}
        </h2>
        {usage && usage.by_model.length > 0 ? (
          <ModelBars buckets={usage.by_model} />
        ) : (
          <EmptyBlock label={t('admin.usage.noRequests')} />
        )}
      </section>

      {/* By agent */}
      <section>
        <h2 className="mb-2 text-[14px] font-semibold text-[var(--color-foreground)]">
          {t('admin.usage.byAgent')} <span className="text-[12px] font-normal text-[var(--color-foreground-muted)]">{t('admin.usage.topFive')}</span>
        </h2>
        {usage && usage.by_agent.length > 0 ? (
          <AgentList buckets={usage.by_agent.slice(0, 5)} />
        ) : (
          <EmptyBlock label={t('admin.usage.noAgentRequests')} />
        )}
      </section>
    </div>
  )
}

function SummaryCard({
  value, label, hint,
}: { value: string; label: string; hint?: string }) {
  return (
    <div className="rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-surface-elevated)] p-4 shadow-whisper">
      <p className="text-[20px] font-bold tracking-tight text-[var(--color-foreground)]">
        {value}
      </p>
      <p className="mt-0.5 text-[12px] text-[var(--color-foreground-muted)]">
        {label}
      </p>
      {hint && (
        <p className="mt-1 text-[10px] text-[var(--color-foreground-subtle)]">
          {hint}
        </p>
      )}
    </div>
  )
}

function ModelBars({ buckets }: { buckets: UsageBucket[] }) {
  const { t } = useLocale()
  const max = Math.max(...buckets.map(b => b.request_count), 1)
  return (
    <div className="rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-surface-elevated)] p-4 shadow-whisper">
      <div className="flex flex-col gap-3">
        {buckets.map(b => {
          const pct = Math.max((b.request_count / max) * 100, 1.5)
          return (
            <div key={b.key}>
              <div className="mb-0.5 flex items-baseline justify-between gap-2">
                <span className="text-[13px] font-medium text-[var(--color-foreground)]">
                  {b.key}
                </span>
                <span className="text-[12px] text-[var(--color-foreground-muted)]">
                  {t('admin.usage.requestCount', { count: formatCount(b.request_count) })}
                </span>
              </div>
              <div className="relative h-2 overflow-hidden rounded-[var(--radius-sm)] bg-[var(--color-surface-alt)]">
                <div
                  className="h-full rounded-[var(--radius-sm)] bg-[var(--color-accent)]"
                  style={{ width: `${pct}%` }}
                />
              </div>
              <p className="mt-0.5 text-[11px] text-[var(--color-foreground-muted)]">
                {t('admin.usage.tokenBreakdown', { prompt: formatTokens(b.prompt_tokens), completion: formatTokens(b.completion_tokens) })}
              </p>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function AgentList({ buckets }: { buckets: UsageBucket[] }) {
  const { t } = useLocale()
  return (
    <div className="rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-surface-elevated)] shadow-whisper">
      <ul className="divide-y divide-[var(--color-border)]">
        {buckets.map(b => (
          <li key={b.key} className="flex items-center justify-between px-4 py-2.5">
            <code className="text-[12px] text-[var(--color-foreground-muted)]">
              {b.key}
            </code>
            <span className="text-[13px] text-[var(--color-foreground)]">
              {t('admin.usage.requestCount', { count: formatCount(b.request_count) })}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function EmptyBlock({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-3 rounded-[var(--radius-md)] border border-dashed border-[var(--color-border)] px-4 py-6">
      <BarChart3 className="h-5 w-5 text-[var(--color-foreground-subtle)]" />
      <p className="text-[13px] text-[var(--color-foreground-muted)]">{label}</p>
    </div>
  )
}

function formatCount(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`
  return String(n)
}

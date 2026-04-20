import { useState } from 'react'
import { BarChart3, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useGatewayUsage, type UsageBucket } from '@/hooks/useLLMGateway'
import { cn } from '@/lib/utils'

/**
 * Usage aggregates — rendered as a 3-card summary + horizontal bars
 * for the top models + a plain list for the top agents. MVP skips
 * real cost estimation ($--) until Phase 5 wires pricing data.
 */

const WINDOWS = [
  { value: '1h', label: '1 hour' },
  { value: '24h', label: '24 hours' },
  { value: '7d', label: '7 days' },
  { value: '30d', label: '30 days' },
]

export function UsageSection() {
  const [window, setWindow] = useState('24h')
  const { usage, status, error, refresh } = useGatewayUsage(window)

  const totalTokens = usage
    ? usage.by_model.reduce(
      (sum, b) => sum + b.prompt_tokens + b.completion_tokens, 0,
    )
    : 0

  return (
    <div className="mx-auto max-w-3xl px-6 py-6">
      <header className="mb-6 flex items-start justify-between">
        <div>
          <h1 className="text-[22px] font-bold tracking-tight text-[var(--color-foreground)]">
            Usage
          </h1>
          <p className="mt-1 text-[13px] text-[var(--color-foreground-muted)]">
            Aggregated traffic through the gateway.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={window}
            onChange={e => setWindow(e.target.value)}
            className="h-9 rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-white px-2.5 text-[13px]"
          >
            {WINDOWS.map(w => (
              <option key={w.value} value={w.value}>{w.label}</option>
            ))}
          </select>
          <Button variant="ghost" size="sm" onClick={refresh}>
            <RefreshCw className={cn('mr-1 h-3.5 w-3.5', status === 'loading' && 'animate-spin')} />
            Refresh
          </Button>
        </div>
      </header>

      {status === 'error' && (
        <div className="mb-4 rounded-[var(--radius-md)] border border-red-200 bg-red-50 px-3 py-2 text-[13px] text-red-900">
          Couldn't load usage: {error}
        </div>
      )}

      {/* Summary cards */}
      <div className="mb-6 grid grid-cols-3 gap-3">
        <SummaryCard
          value={formatCount(usage?.total_requests ?? 0)}
          label="requests"
        />
        <SummaryCard
          value={formatTokens(totalTokens)}
          label={`tokens (${usage?.window_hours ?? 0}h)`}
        />
        <SummaryCard
          value="$—"
          label="est. cost"
          hint="Pricing data coming in Phase 5"
        />
      </div>

      {/* By model */}
      <section className="mb-6">
        <h2 className="mb-2 text-[14px] font-semibold text-[var(--color-foreground)]">
          By model
        </h2>
        {usage && usage.by_model.length > 0 ? (
          <ModelBars buckets={usage.by_model} />
        ) : (
          <EmptyBlock label="No requests in this window." />
        )}
      </section>

      {/* By agent */}
      <section>
        <h2 className="mb-2 text-[14px] font-semibold text-[var(--color-foreground)]">
          By agent <span className="text-[12px] font-normal text-[var(--color-foreground-muted)]">(top 5)</span>
        </h2>
        {usage && usage.by_agent.length > 0 ? (
          <AgentList buckets={usage.by_agent.slice(0, 5)} />
        ) : (
          <EmptyBlock label="No agent-initiated requests in this window." />
        )}
      </section>
    </div>
  )
}

function SummaryCard({
  value, label, hint,
}: { value: string; label: string; hint?: string }) {
  return (
    <div className="rounded-[var(--radius-md)] border border-[var(--color-border)] bg-white p-4 shadow-whisper">
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
  const max = Math.max(...buckets.map(b => b.request_count), 1)
  return (
    <div className="rounded-[var(--radius-md)] border border-[var(--color-border)] bg-white p-4 shadow-whisper">
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
                  {formatCount(b.request_count)} req
                </span>
              </div>
              <div className="relative h-2 overflow-hidden rounded-[var(--radius-sm)] bg-black/5">
                <div
                  className="h-full rounded-[var(--radius-sm)] bg-[var(--color-accent)]"
                  style={{ width: `${pct}%` }}
                />
              </div>
              <p className="mt-0.5 text-[11px] text-[var(--color-foreground-muted)]">
                prompt {formatTokens(b.prompt_tokens)}  ·  completion {formatTokens(b.completion_tokens)}
              </p>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function AgentList({ buckets }: { buckets: UsageBucket[] }) {
  return (
    <div className="rounded-[var(--radius-md)] border border-[var(--color-border)] bg-white shadow-whisper">
      <ul className="divide-y divide-[var(--color-border)]">
        {buckets.map(b => (
          <li key={b.key} className="flex items-center justify-between px-4 py-2.5">
            <code className="text-[12px] text-[var(--color-foreground-muted)]">
              {b.key}
            </code>
            <span className="text-[13px] text-[var(--color-foreground)]">
              {formatCount(b.request_count)} req
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

import { useId, useMemo, useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { useLocale } from '@/i18n/LocaleProvider'
import { executionTraceFrom, traceTotalsLabels } from '@/lib/executionTrace'

/**
 * #880 — the work behind an agent reply: the sentences it wrote between
 * pieces of work, each followed by how much of what it did. Collapsed by
 * default so the reply stays the first thing read.
 */
export default function ExecutionTrace({ metadata }: { metadata?: Record<string, unknown> }) {
  const { t } = useLocale()
  const trace = useMemo(() => executionTraceFrom(metadata), [metadata])
  const [open, setOpen] = useState(false)
  const stepsId = useId()
  if (!trace) return null
  const totals = traceTotalsLabels(t, trace.totals).join(' · ')
  return (
    <div className="mt-2" data-testid="execution-trace">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={open ? stepsId : undefined}
        title={open ? t('chat.traceHide') : t('chat.traceShow')}
        onClick={() => setOpen(value => !value)}
        className="-ml-1 flex min-h-[var(--control-sm-height)] max-w-full items-center gap-1 rounded-[var(--radius-sm)] px-1 text-left text-caption text-[var(--color-foreground-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-brand-focus)]"
      >
        {open
          ? <ChevronDown className="h-4 w-4 shrink-0" aria-hidden="true" />
          : <ChevronRight className="h-4 w-4 shrink-0" aria-hidden="true" />}
        <span className="min-w-0">{`${t('chat.traceToggle')} · ${totals}`}</span>
      </button>
      {open && (
        <ol id={stepsId} className="mt-1 ml-2 space-y-2 border-l border-[var(--color-border)] pl-3">
          {trace.truncated > 0 && (
            <li className="text-caption text-[var(--color-foreground-subtle)]">
              {t('chat.traceTruncated', { count: trace.truncated })}
            </li>
          )}
          {trace.steps.map((step, index) => (
            <li key={index} className="space-y-0.5">
              {step.narration && (
                <p className="whitespace-pre-wrap break-words text-caption text-[var(--color-foreground)]">
                  {step.narration}
                </p>
              )}
              {step.activities && (
                <p className="text-caption text-[var(--color-foreground-muted)]">
                  {traceTotalsLabels(t, step.activities).join(' · ')}
                </p>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}

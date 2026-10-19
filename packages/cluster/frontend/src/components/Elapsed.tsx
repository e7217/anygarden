import { useLocale } from '@/i18n/LocaleProvider'
import { formatElapsed, useNow } from '@/lib/elapsed'

/**
 * #893 — a running "2m 31s" counted from *since* (browser clock, ms). Only
 * this span re-renders every second, and its timer stops once it unmounts.
 */
export default function Elapsed({ since, className = '' }: { since: number; className?: string }) {
  const { t } = useLocale()
  const now = useNow(1000)
  return (
    <span className={`tabular-nums ${className}`} data-testid="work-elapsed">
      {formatElapsed(t, now - since)}
    </span>
  )
}

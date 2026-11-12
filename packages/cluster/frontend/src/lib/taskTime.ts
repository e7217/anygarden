// Coarse task times for the right rail: "23m" running, "2 hours ago" done.
import type { Locale } from '@/i18n/LocaleProvider'
import type { MessageKey } from '@/i18n/messages'

type Translate = (key: MessageKey, vars?: Record<string, string | number>) => string

const MINUTE = 60_000
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

/** Milliseconds since *iso*, or ``null`` when the stamp is missing or malformed. */
export function msSince(iso: string | null | undefined, now: number): number | null {
  if (!iso) return null
  const at = Date.parse(iso)
  return Number.isNaN(at) ? null : Math.max(0, now - at)
}

/** "23m" / "2h" / "3d" (ko: "23분" / "2시간" / "3일"); the row only needs one unit. */
export function formatSpan(t: Translate, ms: number): string {
  if (ms < HOUR) return t('tasks.spanMinutes', { n: Math.max(1, Math.floor(ms / MINUTE)) })
  if (ms < DAY) return t('tasks.spanHours', { n: Math.floor(ms / HOUR) })
  return t('tasks.spanDays', { n: Math.floor(ms / DAY) })
}

/** How long a running task has been going: "23m" (ko: "23분째"). */
export function formatRunning(t: Translate, ms: number): string {
  return ms < MINUTE ? t('tasks.justStarted') : t('tasks.runningFor', { time: formatSpan(t, ms) })
}

/** "2 hours ago" / "yesterday"; a date once it is more than a week old. */
export function formatAgo(
  t: Translate,
  locale: Locale,
  formatDate: (value: Date, options?: Intl.DateTimeFormatOptions) => string,
  iso: string,
  now: number,
): string {
  const ms = msSince(iso, now) ?? 0
  if (ms < MINUTE) return t('tasks.justNow')
  const relative = new Intl.RelativeTimeFormat(locale, { numeric: 'auto' })
  if (ms < HOUR) return relative.format(-Math.floor(ms / MINUTE), 'minute')
  if (ms < DAY) return relative.format(-Math.floor(ms / HOUR), 'hour')
  if (ms < 7 * DAY) return relative.format(-Math.floor(ms / DAY), 'day')
  return formatDate(new Date(iso), { month: 'short', day: 'numeric' })
}

/** A timeline stamp: the time alone today, the date and time otherwise. */
export function formatStamp(
  formatDate: (value: Date, options?: Intl.DateTimeFormatOptions) => string,
  iso: string,
  now: number,
): string {
  const at = new Date(iso)
  const sameDay = at.toDateString() === new Date(now).toDateString()
  return sameDay
    ? formatDate(at, { hour: 'numeric', minute: '2-digit' })
    : formatDate(at, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

// #893 — how long an agent has been working, and how long a reply took.
import { useEffect, useState } from 'react'
import type { MessageKey } from '@/i18n/messages'

type Translate = (key: MessageKey, vars?: Record<string, string | number>) => string

/** "12s" / "2m 31s" / "1h 5m" (ko: "12초" / "2분 31초" / "1시간 5분"). */
export function formatElapsed(t: Translate, ms: number): string {
  const total = Math.max(0, Math.floor((Number.isFinite(ms) ? ms : 0) / 1000))
  if (total < 60) return t('chat.elapsedSeconds', { s: total })
  if (total < 3600) return t('chat.elapsedMinutes', { m: Math.floor(total / 60), s: total % 60 })
  return t('chat.elapsedHours', { h: Math.floor(total / 3600), m: Math.floor((total % 3600) / 60) })
}

/** Re-calibrations closer than this keep the old start, so the time never jitters. */
export const SINCE_HYSTERESIS_MS = 1500

/**
 * The browser-clock start of a run the server says has lasted *elapsedMs*.
 * The server sends a relative value so clock skew does not matter; network
 * delay makes each estimate a little late, so small corrections are ignored.
 */
export function calibrateSince(previous: number | undefined, elapsedMs: number, now: number): number {
  const candidate = now - elapsedMs
  if (previous !== undefined && Math.abs(candidate - previous) < SINCE_HYSTERESIS_MS) return previous
  return candidate
}

/** ``elapsed_ms`` from a typing frame, or ``null`` when absent or malformed. */
export function elapsedMsFrom(frame: { elapsed_ms?: unknown }): number | null {
  const value = frame.elapsed_ms
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null
}

/** The current time, refreshed every *intervalMs* while the caller is mounted. */
export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), intervalMs)
    return () => clearInterval(timer)
  }, [intervalMs])
  return now
}

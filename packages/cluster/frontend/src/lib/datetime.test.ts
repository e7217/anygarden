import { describe, it, expect } from 'vitest'
import { parseServerDate, formatMessageTimestamp } from './datetime'

describe('parseServerDate', () => {
  it('treats a designator-less ISO string as UTC', () => {
    const d = parseServerDate('2026-04-17T05:12:03.456789')
    expect(d.getTime()).toBe(Date.UTC(2026, 3, 17, 5, 12, 3, 456))
  })

  it('keeps a Z-suffixed ISO string as UTC', () => {
    const d = parseServerDate('2026-04-17T05:12:03Z')
    expect(d.getTime()).toBe(Date.UTC(2026, 3, 17, 5, 12, 3))
  })

  it('respects an explicit +09:00 offset', () => {
    const d = parseServerDate('2026-04-17T14:12:03+09:00')
    expect(d.getTime()).toBe(Date.UTC(2026, 3, 17, 5, 12, 3))
  })

  it('respects a compact +0900 offset', () => {
    const d = parseServerDate('2026-04-17T14:12:03+0900')
    expect(d.getTime()).toBe(Date.UTC(2026, 3, 17, 5, 12, 3))
  })

  it('respects a negative offset', () => {
    const d = parseServerDate('2026-04-17T00:12:03-05:00')
    expect(d.getTime()).toBe(Date.UTC(2026, 3, 17, 5, 12, 3))
  })

  it('returns an invalid Date for garbage input (NaN time)', () => {
    const d = parseServerDate('not-a-date')
    expect(Number.isNaN(d.getTime())).toBe(true)
  })
})

describe('formatMessageTimestamp', () => {
  // ``now`` is derived from the message instant so the local-calendar-day
  // comparison is stable regardless of the test runner's timezone: both
  // ``d`` and ``now`` convert to local time with the same offset, so a
  // fixed instant delta shifts the local calendar date predictably.
  const timeOf = (iso: string) =>
    parseServerDate(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

  it('shows only the time for a message earlier on the same calendar day', () => {
    const iso = '2026-07-03T12:00:00Z'
    const d = parseServerDate(iso)
    // ``now`` is a DIFFERENT instant on the SAME local calendar day: it is
    // built from d's own local Y/M/D (so it never crosses the day boundary
    // in any runner timezone) at 18:07:13 — a wall-clock that no whole/¼-hour
    // UTC offset can map 12:00:00Z onto, so now.getTime() !== d.getTime()
    // everywhere. This proves the calendar-day branch, not same-instant
    // equality, drives the time-only result.
    const now = new Date(d.getFullYear(), d.getMonth(), d.getDate(), 18, 7, 13)
    expect(now.getTime()).not.toBe(d.getTime())
    expect(formatMessageTimestamp(iso, now)).toBe(timeOf(iso))
  })

  it('prefixes month/day for a past day in the same year', () => {
    const iso = '2026-03-10T12:00:00Z'
    const d = parseServerDate(iso)
    const now = new Date(d.getTime() + 5 * 86_400_000) // 5 days later, same year
    const expected = `${d.getMonth() + 1}월 ${d.getDate()}일 ${timeOf(iso)}`
    expect(formatMessageTimestamp(iso, now)).toBe(expected)
  })

  it('prefixes year/month/day for a past day in a different year', () => {
    const iso = '2025-12-30T12:00:00Z'
    const d = parseServerDate(iso)
    const now = new Date(d.getTime() + 400 * 86_400_000) // > 1 year later
    const expected = `${d.getFullYear()}년 ${d.getMonth() + 1}월 ${d.getDate()}일 ${timeOf(iso)}`
    expect(formatMessageTimestamp(iso, now)).toBe(expected)
  })

  it('returns an empty string for unparseable input', () => {
    expect(formatMessageTimestamp('not-a-date', new Date())).toBe('')
  })
})

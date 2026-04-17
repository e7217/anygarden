import { describe, it, expect } from 'vitest'
import { parseServerDate } from './datetime'

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

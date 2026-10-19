import { describe, expect, it } from 'vitest'
import { calibrateSince, elapsedMsFrom, formatElapsed, SINCE_HYSTERESIS_MS } from './elapsed'
import { en as enCatalog, ko as koCatalog } from '@/i18n/messages'

const translator = (locale: 'en' | 'ko') => (key: string, vars?: Record<string, string | number>) =>
  Object.entries(vars ?? {}).reduce(
    (text, [name, value]) => text.replace(`{{${name}}}`, String(value)),
    (locale === 'en' ? enCatalog : koCatalog as Record<string, string>)[key as keyof typeof enCatalog],
  )

describe('[ENG-13] formatElapsed', () => {
  const en = translator('en')
  const ko = translator('ko')

  it('switches units at the minute and hour boundaries', () => {
    expect(formatElapsed(en, 0)).toBe('0s')
    expect(formatElapsed(en, 59_999)).toBe('59s')
    expect(formatElapsed(en, 60_000)).toBe('1m 0s')
    expect(formatElapsed(en, 151_000)).toBe('2m 31s')
    expect(formatElapsed(en, 3_599_000)).toBe('59m 59s')
    expect(formatElapsed(en, 3_600_000)).toBe('1h 0m')
    expect(formatElapsed(en, 3_900_000)).toBe('1h 5m')
  })

  it('uses Korean units in Korean', () => {
    expect(formatElapsed(ko, 12_000)).toBe('12초')
    expect(formatElapsed(ko, 151_000)).toBe('2분 31초')
    expect(formatElapsed(ko, 3_900_000)).toBe('1시간 5분')
  })

  it('never shows a negative or broken time', () => {
    expect(formatElapsed(en, -500)).toBe('0s')
    expect(formatElapsed(en, Number.NaN)).toBe('0s')
  })
})

describe('[ENG-13] calibrateSince', () => {
  it('starts from the server count', () => {
    expect(calibrateSince(undefined, 30_000, 100_000)).toBe(70_000)
  })

  it('keeps the old start for small corrections so the time does not jitter', () => {
    expect(calibrateSince(70_000, 30_000 - (SINCE_HYSTERESIS_MS - 1), 100_000)).toBe(70_000)
    expect(calibrateSince(70_000, 29_200, 100_000)).toBe(70_000)
  })

  it('adopts a large correction, such as a new turn', () => {
    expect(calibrateSince(70_000, 0, 100_000)).toBe(100_000)
    expect(calibrateSince(70_000, 40_000, 100_000)).toBe(60_000)
  })
})

describe('[ENG-13] elapsedMsFrom', () => {
  it('accepts only a non-negative number', () => {
    expect(elapsedMsFrom({ elapsed_ms: 1200 })).toBe(1200)
    expect(elapsedMsFrom({ elapsed_ms: 0 })).toBe(0)
    expect(elapsedMsFrom({})).toBeNull()
    expect(elapsedMsFrom({ elapsed_ms: null })).toBeNull()
    expect(elapsedMsFrom({ elapsed_ms: -1 })).toBeNull()
    expect(elapsedMsFrom({ elapsed_ms: '12' })).toBeNull()
  })
})

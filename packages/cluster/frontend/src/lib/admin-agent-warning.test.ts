import { describe, expect, it } from 'vitest'
import { shouldShowFallbackCrashWarning } from './admin-agent-warning'

const base = {
  actual_state: 'crashed',
  last_crash_reason: 'engine exited',
  unavailable_reason: null,
}

describe('shouldShowFallbackCrashWarning', () => {
  it('shows a legacy reason for an actual crash', () => {
    expect(shouldShowFallbackCrashWarning(base)).toBe(true)
  })

  it.each(['pending', 'starting', 'stopping', 'stopped', 'running', 'idle'])(
    'suppresses stale crash text while state is %s',
    actual_state => {
      expect(
        shouldShowFallbackCrashWarning({ ...base, actual_state }),
      ).toBe(false)
    },
  )

  it('does not render an empty fallback', () => {
    expect(
      shouldShowFallbackCrashWarning({
        ...base,
        last_crash_reason: null,
      }),
    ).toBe(false)
  })

  it('defers to the structured unavailability reason', () => {
    expect(
      shouldShowFallbackCrashWarning({
        ...base,
        unavailable_reason: {
          code: 'SPAWN_FAILED',
          message: 'Spawn failed',
        },
      }),
    ).toBe(false)
  })
})

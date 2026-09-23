import { describe, expect, it } from 'vitest'
import { unsupportedEngineVersionWarning } from './engineVersion'

describe('unsupportedEngineVersionWarning (#687)', () => {
  it('returns null when the detected version is supported', () => {
    expect(unsupportedEngineVersionWarning('pi-cli', '0.85.1', ['0.85.1'])).toBeNull()
    expect(unsupportedEngineVersionWarning('codex-cli', 'codex-cli 0.155.1', ['0.154.0', '0.155.1'])).toBeNull()
  })

  it('names observed and required versions on mismatch', () => {
    expect(unsupportedEngineVersionWarning('pi-cli', '0.87.1', ['0.85.1'])).toBe(
      'This machine has pi-cli 0.87.1, but this build requires 0.85.1. Agents will fail with UNSUPPORTED_RUNTIME until the supported version is installed.',
    )
    expect(unsupportedEngineVersionWarning('codex-cli', 'codex-cli 0.160.0', ['0.154.0', '0.155.1'])).toContain(
      'requires one of 0.154.0, 0.155.1',
    )
  })

  it('does not treat a longer patch number as a match', () => {
    expect(unsupportedEngineVersionWarning('pi-cli', '0.85.10', ['0.85.1'])).not.toBeNull()
  })

  it('stays quiet without a gate or a detected version', () => {
    expect(unsupportedEngineVersionWarning('pi-cli', '0.87.1', [])).toBeNull()
    expect(unsupportedEngineVersionWarning('pi-cli', null, ['0.85.1'])).toBeNull()
    expect(unsupportedEngineVersionWarning('pi-cli', 'unknown', ['0.85.1'])).toBeNull()
  })
})

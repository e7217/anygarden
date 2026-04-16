import { describe, it, expect } from 'vitest'
import {
  deriveAgentOnline,
  agentStatusLabel,
  ALIVE_AGENT_STATES,
} from './agent-liveness'

describe('ALIVE_AGENT_STATES', () => {
  it('includes running and starting', () => {
    expect(ALIVE_AGENT_STATES.has('running')).toBe(true)
    expect(ALIVE_AGENT_STATES.has('starting')).toBe(true)
  })
})

describe('deriveAgentOnline', () => {
  it('1) treats "running" as online', () => {
    expect(deriveAgentOnline('running')).toBe(true)
  })

  it('2) treats "starting" as online (still booting but alive)', () => {
    expect(deriveAgentOnline('starting')).toBe(true)
  })

  it('3) treats "stopped" as offline', () => {
    expect(deriveAgentOnline('stopped')).toBe(false)
  })

  it('4) treats "crashed" as offline', () => {
    expect(deriveAgentOnline('crashed')).toBe(false)
  })

  it('5) treats "idle" as offline', () => {
    expect(deriveAgentOnline('idle')).toBe(false)
  })

  it('6) treats "pending" as offline', () => {
    expect(deriveAgentOnline('pending')).toBe(false)
  })

  it('7) treats undefined actualState as offline', () => {
    expect(deriveAgentOnline(undefined)).toBe(false)
  })

  it('8) forces offline when the hosting machine is offline, even if actualState is "running"', () => {
    expect(deriveAgentOnline('running', { machineOffline: true })).toBe(false)
  })
})

describe('agentStatusLabel', () => {
  it('9) returns the raw actualState string when machine is reachable', () => {
    expect(agentStatusLabel('running')).toBe('running')
  })

  it('10) returns "unreachable" when the hosting machine is offline, regardless of actualState', () => {
    expect(agentStatusLabel(undefined, { machineOffline: true })).toBe(
      'unreachable',
    )
    expect(agentStatusLabel('running', { machineOffline: true })).toBe(
      'unreachable',
    )
  })

  it('falls back to "unknown" for undefined input on a reachable machine', () => {
    expect(agentStatusLabel(undefined)).toBe('unknown')
  })
})

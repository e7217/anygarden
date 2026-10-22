import { describe, expect, it } from 'vitest'
import {
  NODE_RESTART_STALL_MS,
  nextNodeUpdateProgress,
  type NodeUpdateProgress,
  type NodeUpdateState,
} from './useSystemVersion'

const idle: NodeUpdateProgress = {
  phase: 'idle',
  fromVersion: null,
  targetVersion: null,
  version: null,
  error: null,
  since: 0,
}

function server(status: NodeUpdateState['status'], from_version = '0.23.0', error: string | null = null): NodeUpdateState {
  return { supported: true, reason: null, status, from_version, target_version: '0.24.0', error }
}

describe('nextNodeUpdateProgress (#927)', () => {
  it('[NODE-18] follows install → unreachable restart → back on the new version', () => {
    let p = nextNodeUpdateProgress(idle, server('installing'), 1000)
    expect(p).toMatchObject({ phase: 'installing', fromVersion: '0.23.0', targetVersion: '0.24.0' })
    p = nextNodeUpdateProgress(p, server('restarting'), 2000)
    expect(p.phase).toBe('restarting')
    p = nextNodeUpdateProgress(p, null, 3000)
    expect(p.phase).toBe('restarting')
    p = nextNodeUpdateProgress(p, server('idle', '0.24.0'), 4000)
    expect(p).toMatchObject({ phase: 'updated', version: '0.24.0' })
  })

  it('[NODE-18] flags a node that restarted on the old version', () => {
    let p = nextNodeUpdateProgress(idle, server('restarting'), 1000)
    p = nextNodeUpdateProgress(p, server('idle', '0.23.0'), 2000)
    expect(p).toMatchObject({ phase: 'same_version', version: '0.23.0' })
  })

  it('[NODE-18] reports a failed install from the node', () => {
    const p = nextNodeUpdateProgress(idle, server('failed', '0.23.0', 'no matching distribution'), 1000)
    expect(p).toMatchObject({ phase: 'failed', error: 'no matching distribution' })
  })

  it('[NODE-18] calls a node that stays away too long stalled, measured from when it last answered', () => {
    let p = nextNodeUpdateProgress(idle, server('installing'), 0)
    // A long install still answers, so the stall clock restarts.
    p = nextNodeUpdateProgress(p, server('installing'), NODE_RESTART_STALL_MS * 2)
    p = nextNodeUpdateProgress(p, null, NODE_RESTART_STALL_MS * 2 + 1000)
    expect(p.phase).toBe('restarting')
    p = nextNodeUpdateProgress(p, null, NODE_RESTART_STALL_MS * 3 + 1)
    expect(p.phase).toBe('stalled')
    // It can still come back.
    p = nextNodeUpdateProgress(p, server('idle', '0.24.0'), NODE_RESTART_STALL_MS * 4)
    expect(p.phase).toBe('updated')
  })

  it('ignores failed polls when nothing is running', () => {
    expect(nextNodeUpdateProgress(idle, null, 1000)).toBe(idle)
    expect(nextNodeUpdateProgress(idle, server('idle'), 1000)).toBe(idle)
  })
})

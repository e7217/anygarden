import { describe, expect, it } from 'vitest'
import { executionTraceFrom, traceTotalsLabels } from './executionTrace'

const t = (key: string, vars?: Record<string, string | number>) =>
  vars ? `${key}(${Object.values(vars).join('/')})` : key

describe('[ENG-11] executionTraceFrom', () => {
  it('reads the steps and totals an agent reply carries', () => {
    expect(executionTraceFrom({
      execution_trace: {
        v: 1,
        steps: [{ narration: 'Checking.', activities: { commands: 2 } }, { activities: { file_changes: 1 } }],
        totals: { commands: 2, file_changes: 1 },
        truncated: 3,
      },
    })).toEqual({
      steps: [{ narration: 'Checking.', activities: { commands: 2 } }, { activities: { file_changes: 1 } }],
      totals: { commands: 2, file_changes: 1 },
      truncated: 3,
    })
  })

  it('ignores anything outside the closed shape', () => {
    expect(executionTraceFrom(null)).toBeNull()
    expect(executionTraceFrom({ execution_trace: { v: 2, steps: [], totals: { commands: 1 } } })).toBeNull()
    expect(executionTraceFrom({ execution_trace: { v: 1, steps: [], totals: { commands: 0 } } })).toBeNull()
    expect(executionTraceFrom({
      execution_trace: { v: 1, steps: ['x', { narration: 3, activities: { evil: 1 } }], totals: { commands: 1, evil: 2 } },
    })).toEqual({ steps: [], totals: { commands: 1 }, truncated: 0 })
  })

  it('labels totals in a stable order with singular and plural forms', () => {
    expect(traceTotalsLabels(t, { file_changes: 2, commands: 1 })).toEqual([
      'chat.traceCommandsOne(1)', 'chat.traceFileChangesMany(2)',
    ])
  })
})

describe('[ENG-13] executionTraceFrom duration', () => {
  const base = { v: 1, steps: [], totals: { commands: 1 } }

  it('reads the total working time when present', () => {
    expect(executionTraceFrom({ execution_trace: { ...base, duration_ms: 754_000 } })?.durationMs).toBe(754_000)
    expect(executionTraceFrom({ execution_trace: { ...base, duration_ms: 0 } })?.durationMs).toBe(0)
  })

  it('leaves it out when missing or malformed, keeping the trace', () => {
    for (const duration_ms of [undefined, -1, 1.5, '12', null]) {
      const trace = executionTraceFrom({ execution_trace: { ...base, duration_ms } })
      expect(trace).not.toBeNull()
      expect(trace).not.toHaveProperty('durationMs')
    }
  })
})

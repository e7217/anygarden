// #880 — an agent reply's ``metadata.execution_trace``: the sentences the
// agent wrote between pieces of work and how much of each kind it did. The
// server keeps only this closed shape; parse defensively all the same.
import type { MessageKey } from '@/i18n/messages'

export type TraceTally = Partial<Record<TraceKind, number>>

export interface TraceStep {
  narration?: string
  activities?: TraceTally
}

export interface ExecutionTrace {
  steps: TraceStep[]
  totals: TraceTally
  /** Earlier steps dropped to stay within the size budget. */
  truncated: number
  /** #893 — the turn's total working time; absent from older agents. */
  durationMs?: number
}

const TRACE_KINDS = ['commands', 'file_changes', 'file_reads', 'anygarden_tools', 'tools', 'web_searches'] as const
type TraceKind = (typeof TRACE_KINDS)[number]

function tallyFrom(raw: unknown): TraceTally {
  const tally: TraceTally = {}
  if (!raw || typeof raw !== 'object') return tally
  for (const kind of TRACE_KINDS) {
    const count = (raw as Record<string, unknown>)[kind]
    if (typeof count === 'number' && Number.isInteger(count) && count > 0) tally[kind] = count
  }
  return tally
}

export function executionTraceFrom(metadata?: Record<string, unknown> | null): ExecutionTrace | null {
  const raw = metadata?.execution_trace
  if (!raw || typeof raw !== 'object') return null
  const { v, steps, totals, truncated, duration_ms: duration } = raw as Record<string, unknown>
  const tally = tallyFrom(totals)
  if (v !== 1 || !Object.keys(tally).length) return null
  const parsed: TraceStep[] = []
  for (const item of Array.isArray(steps) ? steps : []) {
    if (!item || typeof item !== 'object') continue
    const { narration, activities } = item as Record<string, unknown>
    const step: TraceStep = {}
    if (typeof narration === 'string' && narration.trim()) step.narration = narration
    const work = tallyFrom(activities)
    if (Object.keys(work).length) step.activities = work
    if (step.narration || step.activities) parsed.push(step)
  }
  return {
    steps: parsed,
    totals: tally,
    truncated: typeof truncated === 'number' && Number.isInteger(truncated) && truncated > 0 ? truncated : 0,
    ...(typeof duration === 'number' && Number.isInteger(duration) && duration >= 0 ? { durationMs: duration } : {}),
  }
}

const TALLY_KEYS: Record<TraceKind, [MessageKey, MessageKey]> = {
  commands: ['chat.traceCommandsOne', 'chat.traceCommandsMany'],
  file_changes: ['chat.traceFileChangesOne', 'chat.traceFileChangesMany'],
  file_reads: ['chat.traceFileReadsOne', 'chat.traceFileReadsMany'],
  anygarden_tools: ['chat.traceAnygardenToolsOne', 'chat.traceAnygardenToolsMany'],
  tools: ['chat.traceToolsOne', 'chat.traceToolsMany'],
  web_searches: ['chat.traceWebSearchesOne', 'chat.traceWebSearchesMany'],
}

type Translate = (key: MessageKey, vars?: Record<string, string | number>) => string

/** "3 shell commands", "1 file changed", … in a fixed order. */
export function traceTotalsLabels(t: Translate, tally: TraceTally): string[] {
  return TRACE_KINDS.flatMap(kind => {
    const count = tally[kind]
    if (!count) return []
    const [one, many] = TALLY_KEYS[kind]
    return [t(count === 1 ? one : many, { count })]
  })
}

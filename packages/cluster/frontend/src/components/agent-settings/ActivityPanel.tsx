import { useEffect, useMemo, useState } from 'react'
import { ChevronRight } from 'lucide-react'
import { apiFetch } from '@/lib/api'

interface ActivityLog {
  id: string
  event_type: string
  timestamp: string
  // #222 — the turn-correlation id. Null for system events
  // (start_requested / stop_requested / state_changed) that don't
  // belong to any particular request lifecycle.
  request_id: string | null
  details: Record<string, unknown> | null
}

interface Props {
  agentId: string | null
}

// A turn is the cluster's bookkeeping unit for "one user input → agent
// reaction". Its server-side fingerprint is a shared ``request_id``
// stamped on every related ActivityLog row. We group client-side
// because the API already exposes every field we need; a dedicated
// turns endpoint would duplicate that logic (#222 §3.2).
interface Turn {
  requestId: string
  events: ActivityLog[]
  firstTs: number
  lastTs: number
  outcome: TurnOutcome
  triggerMessageId: string | null
}

type TurnOutcome = 'responded' | 'silent' | 'orphaned' | 'in_flight'

function deriveOutcome(events: ActivityLog[]): TurnOutcome {
  const kinds = new Set(events.map(e => e.event_type))
  if (kinds.has('handler_orphaned')) return 'orphaned'
  if (kinds.has('response_sent')) return 'responded'
  if (kinds.has('handler_finished')) return 'silent'
  return 'in_flight'
}

// #222 — system events (start_requested / stop_requested /
// state_changed / replacement_requested) are lifecycle-independent
// so they land in a dedicated "System" section rather than forming a
// null-request_id pseudo-turn.
export function splitLogs(
  logs: ActivityLog[],
): { turns: Turn[]; system: ActivityLog[] } {
  const byRequest = new Map<string, ActivityLog[]>()
  const system: ActivityLog[] = []
  for (const row of logs) {
    if (!row.request_id) {
      system.push(row)
      continue
    }
    const bucket = byRequest.get(row.request_id)
    if (bucket) bucket.push(row)
    else byRequest.set(row.request_id, [row])
  }
  const turns: Turn[] = []
  for (const [requestId, rawEvents] of byRequest.entries()) {
    const events = [...rawEvents].sort((a, b) =>
      a.timestamp.localeCompare(b.timestamp),
    )
    const firstTs = new Date(events[0].timestamp).getTime()
    const lastTs = new Date(events[events.length - 1].timestamp).getTime()
    const triggerRow = events.find(e => e.event_type === 'message_received')
    const triggerMessageId =
      triggerRow && typeof triggerRow.details?.trigger_message_id === 'string'
        ? (triggerRow.details.trigger_message_id as string)
        : null
    turns.push({
      requestId,
      events,
      firstTs,
      lastTs,
      outcome: deriveOutcome(events),
      triggerMessageId,
    })
  }
  // Most recent turn first.
  turns.sort((a, b) => b.firstTs - a.firstTs)
  return { turns, system }
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.max(ms, 0)} ms`
  const s = ms / 1000
  if (s < 60) return `${s.toFixed(s < 10 ? 1 : 0)} s`
  const m = Math.floor(s / 60)
  const rem = Math.round(s - m * 60)
  return `${m}m ${rem}s`
}

function outcomeLabel(outcome: TurnOutcome): string {
  switch (outcome) {
    case 'responded': return 'responded'
    case 'silent': return 'no response'
    case 'orphaned': return 'orphaned'
    case 'in_flight': return 'in flight'
  }
}

function outcomeDotClass(outcome: TurnOutcome): string {
  switch (outcome) {
    case 'responded': return 'bg-[var(--color-success)]'
    case 'silent': return 'bg-[var(--color-warning)]'
    case 'orphaned': return 'bg-[var(--color-destructive,#d74c4c)]'
    case 'in_flight': return 'bg-[var(--color-foreground-muted)]'
  }
}

export default function ActivityPanel({ agentId }: Props) {
  const [logs, setLogs] = useState<ActivityLog[]>([])
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())

  useEffect(() => {
    if (!agentId) return
    let cancelled = false
    apiFetch(`/api/v1/agents/${agentId}/activity?limit=50`).then(async r => {
      if (cancelled || !r.ok) return
      setLogs(await r.json())
    })
    return () => { cancelled = true }
  }, [agentId])

  const { turns, system } = useMemo(() => splitLogs(logs), [logs])

  const toggle = (rid: string) => {
    setExpanded(prev => {
      const next = new Set(prev)
      if (next.has(rid)) next.delete(rid)
      else next.add(rid)
      return next
    })
  }

  if (logs.length === 0) {
    return (
      <div className="py-2" data-testid="activity-panel">
        <p className="text-caption text-[var(--color-foreground-muted)]">
          No activity yet
        </p>
      </div>
    )
  }

  return (
    <div
      className="max-h-[60vh] overflow-y-auto space-y-3 py-2"
      data-testid="activity-panel"
    >
      {turns.length > 0 && (
        <section className="space-y-1.5">
          <h4 className="text-[11px] font-medium uppercase tracking-wide text-[var(--color-foreground-muted)]">
            Turns
          </h4>
          <ul className="space-y-1">
            {turns.map(turn => {
              const isOpen = expanded.has(turn.requestId)
              const duration = turn.lastTs - turn.firstTs
              return (
                <li
                  key={turn.requestId}
                  className="rounded border border-[var(--color-border)] bg-[var(--color-surface)]"
                  data-testid="activity-turn-row"
                >
                  <button
                    type="button"
                    onClick={() => toggle(turn.requestId)}
                    className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-xs"
                  >
                    <ChevronRight
                      className={`h-3 w-3 shrink-0 transition-transform text-[var(--color-foreground-muted)] ${
                        isOpen ? 'rotate-90' : ''
                      }`}
                    />
                    <span
                      className={`inline-block h-1.5 w-1.5 rounded-full shrink-0 ${outcomeDotClass(turn.outcome)}`}
                      aria-label={outcomeLabel(turn.outcome)}
                    />
                    <span className="font-medium text-[var(--color-foreground)]">
                      {new Date(turn.firstTs).toLocaleString()}
                    </span>
                    <span className="text-[var(--color-foreground-muted)]">
                      · {formatDuration(duration)}
                    </span>
                    <span className="text-[var(--color-foreground-muted)]">
                      · {outcomeLabel(turn.outcome)}
                    </span>
                    {turn.triggerMessageId && (
                      <span
                        className="ml-auto truncate text-[10px] text-[var(--color-foreground-subtle)] font-mono"
                        title={`Triggered by message ${turn.triggerMessageId}`}
                      >
                        #{turn.triggerMessageId.slice(0, 6)}
                      </span>
                    )}
                  </button>
                  {isOpen && (
                    <ol className="border-t border-[var(--color-border)] px-6 py-1.5 space-y-0.5">
                      {turn.events.map(evt => (
                        <li
                          key={evt.id}
                          className="flex items-center gap-2 text-[11px]"
                        >
                          <span className="font-mono text-[var(--color-foreground)]">
                            {evt.event_type}
                          </span>
                          <span className="text-[var(--color-foreground-muted)]">
                            {new Date(evt.timestamp).toLocaleTimeString()}
                          </span>
                        </li>
                      ))}
                    </ol>
                  )}
                </li>
              )
            })}
          </ul>
        </section>
      )}

      {system.length > 0 && (
        <section className="space-y-1">
          <h4 className="text-[11px] font-medium uppercase tracking-wide text-[var(--color-foreground-muted)]">
            System events
          </h4>
          <ul className="space-y-1">
            {system.map(evt => {
              // #309 — render permission transitions inline so admins
              // see "from → to" without expanding details JSON. Other
              // system rows keep their compact one-liner.
              const isPermChange = evt.event_type === 'agent_permission_changed'
              const fromTier =
                typeof evt.details?.from === 'string'
                  ? (evt.details.from as string)
                  : evt.details?.from === null
                  ? 'default'
                  : null
              const toTier =
                typeof evt.details?.to === 'string'
                  ? (evt.details.to as string)
                  : evt.details?.to === null
                  ? 'default'
                  : null
              return (
                <li
                  key={evt.id}
                  className="flex items-center gap-2 text-xs"
                  data-testid={
                    isPermChange ? 'activity-permission-row' : undefined
                  }
                >
                  <span className={`inline-block h-1.5 w-1.5 rounded-full shrink-0 ${
                    evt.event_type === 'start_requested'
                      ? 'bg-[var(--color-success)]'
                      : evt.event_type === 'stop_requested'
                      ? 'bg-[var(--color-foreground-subtle)]'
                      : 'bg-[var(--color-warning)]'
                  }`} />
                  <span className="font-medium text-[var(--color-foreground)]">
                    {isPermChange ? 'permission_changed' : evt.event_type}
                  </span>
                  {isPermChange && fromTier && toTier && (
                    <span className="text-[var(--color-foreground-muted)] font-mono">
                      {fromTier} → {toTier}
                    </span>
                  )}
                  <span className="text-[var(--color-foreground-muted)]">
                    {new Date(evt.timestamp).toLocaleString()}
                  </span>
                </li>
              )
            })}
          </ul>
        </section>
      )}
    </div>
  )
}

import { useEffect, useMemo, useState } from 'react'
import { ChevronRight } from 'lucide-react'
import { apiFetch } from '@/lib/api'

export interface ActivityLog {
  id: string
  event_type: string
  timestamp: string
  // #222 — the turn-correlation id. Null for system events
  // (start_requested / stop_requested / state_changed) that don't
  // belong to any particular request lifecycle.
  request_id: string | null
  // #429 — which agent the row belongs to. The per-agent panel knows
  // this implicitly; the room-level view (RoomActivityDialog) needs it
  // to label each turn. Optional so existing callers compile unchanged.
  agent_id?: string | null
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
export interface Turn {
  requestId: string
  events: ActivityLog[]
  firstTs: number
  lastTs: number
  outcome: TurnOutcome
  triggerMessageId: string | null
  agentId: string | null // #429 — owning agent (for the room-level view)
  // #425 — authoritative fields the agent already reports in
  // ``details`` but the UI previously ignored (recomputing duration
  // from row timestamps and mislabelling failed turns as 'responded'
  // because the #422 error notice is itself a response_sent).
  finalOutcome: EngineOutcome | null // handler_finished details.outcome
  durationMs: number | null // handler_finished details.duration_ms (authoritative)
  engine: string | null // engine_call_* details.engine
  roomId: string | null
  error: string | null // failure reason, when the turn failed
}

type TurnOutcome = 'responded' | 'silent' | 'orphaned' | 'in_flight'
type EngineOutcome = 'ok' | 'failed' | 'timeout' | 'cancelled' | 'rejected'

function str(v: unknown): string | null {
  return typeof v === 'string' ? v : null
}
function num(v: unknown): number | null {
  return typeof v === 'number' ? v : null
}

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
    // #425 — pull the authoritative fields the agent already reports.
    let finalOutcome: EngineOutcome | null = null
    let durationMs: number | null = null
    let engine: string | null = null
    let roomId: string | null = null
    let error: string | null = null
    for (const e of events) {
      const d = e.details ?? {}
      roomId = roomId ?? str(d.room_id)
      if (e.event_type === 'engine_call_started' || e.event_type === 'engine_call_finished') {
        engine = str(d.engine) ?? engine
      }
      if (e.event_type === 'handler_finished') {
        finalOutcome = (str(d.outcome) as EngineOutcome | null) ?? finalOutcome
        durationMs = num(d.duration_ms) ?? durationMs
        error = str(d.error) ?? error
      }
      if (e.event_type === 'engine_call_finished') {
        error = error ?? str(d.error)
      }
    }
    const agentId =
      events.map(e => e.agent_id).find((a): a is string => !!a) ?? null
    turns.push({
      requestId,
      events,
      firstTs,
      lastTs,
      outcome: deriveOutcome(events),
      triggerMessageId,
      agentId,
      finalOutcome,
      durationMs,
      engine,
      roomId,
      error,
    })
  }
  // Most recent turn first.
  turns.sort((a, b) => b.firstTs - a.firstTs)
  return { turns, system }
}

export function formatDuration(ms: number): string {
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

// #425 — the agent's reported outcome is authoritative. When present it
// wins over the event-presence heuristic (which mislabels #422 failures
// as 'responded' since the error notice is itself a response_sent).
export function turnLabel(turn: Turn): string {
  const fo = turn.finalOutcome
  if (fo) return fo === 'ok' ? 'responded' : fo
  return outcomeLabel(turn.outcome)
}

export function turnDotClass(turn: Turn): string {
  const fo = turn.finalOutcome
  if (fo) {
    if (fo === 'ok') return 'bg-[var(--color-success)]'
    if (fo === 'cancelled') return 'bg-[var(--color-foreground-muted)]'
    return 'bg-[var(--color-destructive,#d74c4c)]' // failed | timeout | rejected
  }
  return outcomeDotClass(turn.outcome)
}

// #425 — per-event one-line detail (engine / duration / outcome / error)
// pulled from the row's details JSON; '' when the row carries nothing.
function eventDetail(evt: ActivityLog): string {
  const d = evt.details ?? {}
  const parts: string[] = []
  const engine = str(d.engine)
  if (engine) parts.push(engine)
  const dur = num(d.duration_ms)
  if (dur != null) parts.push(formatDuration(dur))
  const outcome = str(d.outcome)
  if (outcome) parts.push(outcome)
  const err = str(d.error)
  if (err) parts.push(err.length > 80 ? err.slice(0, 79) + '…' : err)
  return parts.join(' · ')
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
              // #425 — prefer the agent's authoritative engine time;
              // fall back to the row interval for legacy turns.
              const duration = turn.durationMs ?? turn.lastTs - turn.firstTs
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
                      className={`inline-block h-1.5 w-1.5 rounded-full shrink-0 ${turnDotClass(turn)}`}
                      aria-label={turnLabel(turn)}
                    />
                    <span className="font-medium text-[var(--color-foreground)]">
                      {new Date(turn.firstTs).toLocaleString()}
                    </span>
                    <span className="text-[var(--color-foreground-muted)]">
                      · {formatDuration(duration)}
                    </span>
                    {turn.engine && (
                      <span className="text-[var(--color-foreground-muted)]">
                        · {turn.engine}
                      </span>
                    )}
                    <span className="text-[var(--color-foreground-muted)]">
                      · {turnLabel(turn)}
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
                      {turn.roomId && (
                        <li className="text-[10px] text-[var(--color-foreground-subtle)] font-mono">
                          room {turn.roomId}
                        </li>
                      )}
                      {turn.events.map(evt => {
                        const detail = eventDetail(evt)
                        return (
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
                            {detail && (
                              <span className="truncate text-[var(--color-foreground-muted)]">
                                · {detail}
                              </span>
                            )}
                          </li>
                        )
                      })}
                      {turn.error && (
                        <li className="text-[11px] text-[var(--color-destructive,#d74c4c)]">
                          error: {turn.error}
                        </li>
                      )}
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

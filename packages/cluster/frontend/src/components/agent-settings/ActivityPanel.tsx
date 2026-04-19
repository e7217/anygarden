import { useEffect, useState } from 'react'
import { apiFetch } from '@/lib/api'

interface ActivityLog {
  id: string
  event_type: string
  timestamp: string
  details: Record<string, unknown> | null
}

interface Props {
  agentId: string | null
}

export default function ActivityPanel({ agentId }: Props) {
  const [logs, setLogs] = useState<ActivityLog[]>([])

  useEffect(() => {
    if (!agentId) return
    let cancelled = false
    apiFetch(`/api/v1/agents/${agentId}/activity?limit=50`).then(async r => {
      if (cancelled || !r.ok) return
      setLogs(await r.json())
    })
    return () => { cancelled = true }
  }, [agentId])

  return (
    <div className="max-h-[60vh] overflow-y-auto space-y-1.5 py-2" data-testid="activity-panel">
      {logs.length === 0 ? (
        <p className="text-caption text-[var(--color-foreground-muted)]">No activity yet</p>
      ) : (
        logs.map(evt => (
          <div key={evt.id} className="flex items-center gap-2 text-xs">
            <span className={`inline-block h-1.5 w-1.5 rounded-full ${
              evt.event_type === 'start_requested' ? 'bg-[var(--color-success)]'
                : evt.event_type === 'stop_requested' ? 'bg-[var(--color-foreground-subtle)]'
                : 'bg-[var(--color-warning)]'
            }`} />
            <span className="font-medium text-[var(--color-foreground)]">{evt.event_type}</span>
            <span className="text-[var(--color-foreground-muted)]">
              {new Date(evt.timestamp).toLocaleString()}
            </span>
          </div>
        ))
      )}
    </div>
  )
}

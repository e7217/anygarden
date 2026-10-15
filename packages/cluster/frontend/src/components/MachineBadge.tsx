import { Server } from 'lucide-react'
import type { AgentPlacement } from '@/hooks/useAgents'
import { useLocale } from '@/i18n/LocaleProvider'
import { cn } from '@/lib/utils'

interface MachineBadgeProps {
  placement?: AgentPlacement | null
  online: boolean
  className?: string
}

/**
 * #851 — names the machine an agent runs on when that machine is not this
 * node's own. Local and unplaced agents render nothing, so a single-machine
 * install looks unchanged.
 */
export default function MachineBadge({ placement, online, className }: MachineBadgeProps) {
  const { t } = useLocale()
  if (!placement || placement.is_local) return null

  const host = placement.machine_hostname || placement.machine_name
  const label = t(
    online ? 'chat.agentMachineOnline' : 'chat.agentMachineOffline',
    { name: placement.machine_name, host },
  )
  return (
    <span
      data-testid="agent-machine-badge"
      data-online={online ? 'true' : 'false'}
      title={label}
      aria-label={label}
      className={cn(
        'inline-flex min-w-0 max-w-[8rem] shrink-0 items-center gap-1 rounded-[var(--radius-pill)] bg-[var(--color-surface-hover)] px-1.5 text-badge',
        online
          ? 'text-[var(--color-foreground-muted)]'
          : 'text-[var(--color-foreground-subtle)] opacity-70',
        className,
      )}
    >
      <Server className="h-3 w-3 shrink-0" aria-hidden="true" />
      <span className="min-w-0 truncate">{placement.machine_name}</span>
    </span>
  )
}

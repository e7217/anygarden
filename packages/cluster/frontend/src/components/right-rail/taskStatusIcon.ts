import { CheckCircle2, Circle, Clock, PauseCircle, XCircle } from 'lucide-react'

// #319 — ``blocked`` / ``failed`` are system-set statuses (the goals
// sweeper stamps ``failed`` on pickup/execution timeouts; agents can
// stamp ``blocked`` themselves via ``mark_task_status``). Each status
// keeps its own icon shape so the list never relies on color alone.
export const STATUS_ICON: Record<string, typeof Circle> = {
  todo: Circle,
  in_progress: Clock,
  done: CheckCircle2,
  blocked: PauseCircle,
  failed: XCircle,
  cancelled: XCircle,
  cancelling: PauseCircle,
  superseded: PauseCircle,
}

export function statusIconColor(status: string): string {
  switch (status) {
    case 'done': return 'text-[var(--color-success)]'
    case 'in_progress': return 'text-[var(--color-status-online)]'
    case 'failed': return 'text-[var(--color-danger)]'
    case 'blocked': return 'text-[var(--color-warning)]'
    default: return 'text-[var(--color-foreground-subtle)]'
  }
}

/**
 * TaskAssignmentCard — compact in-stream marker for synthetic
 * task-assignment messages (#266 Step 8).
 *
 * The server drops a single mention-bearing message into the room
 * whenever a task is (re)assigned to an agent. We render it here as
 * a quiet card rather than a normal bubble so the chat stream stays
 * readable. Style follows DESIGN.md (warm neutral, whisper border,
 * single-accent brand for the verb).
 */
import { ClipboardList, ArrowRight } from 'lucide-react'
import type { Participant } from '@/pages/ChatPage'
import type { TaskAssignmentMeta } from '@/lib/taskAssignment'

interface Props {
  meta: TaskAssignmentMeta
  title: string
  assignee: Participant | undefined
}

const EVENT_LABEL: Record<TaskAssignmentMeta['event'], string> = {
  assigned: 'Task assigned',
  reassigned: 'Task reassigned',
}

export default function TaskAssignmentCard({ meta, title, assignee }: Props) {
  const assigneeName = assignee?.display_name
    ?? meta.assignee_pid.slice(0, 8)
  return (
    <div
      data-testid="task-assignment-card"
      className="inline-flex max-w-full items-center gap-2 rounded-[var(--radius-md)] border border-[rgba(0,0,0,0.1)] bg-white px-3 py-1.5 text-sm shadow-[0_1px_2px_rgba(0,0,0,0.03)]"
    >
      <ClipboardList
        className="h-3.5 w-3.5 shrink-0 text-[var(--color-brand)]"
        aria-hidden
      />
      <span className="text-[11px] font-medium uppercase tracking-[0.06em] text-[var(--color-foreground-muted)]">
        {EVENT_LABEL[meta.event]}
      </span>
      <span className="truncate text-[var(--color-foreground)]">
        {title}
      </span>
      <ArrowRight
        className="h-3 w-3 shrink-0 text-[var(--color-foreground-subtle)]"
        aria-hidden
      />
      <span className="truncate text-[var(--color-foreground-muted)]">
        @{assigneeName}
      </span>
    </div>
  )
}

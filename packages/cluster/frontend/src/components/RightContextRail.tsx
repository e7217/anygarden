import { useEffect } from 'react'
import { X } from 'lucide-react'
import { ScrollArea } from '@/components/ui/scroll-area'
import { useRightSidebarLayout } from '@/hooks/useRightSidebarLayout'
import TasksSection from '@/components/right-rail/TasksSection'
import FilesSection from '@/components/right-rail/FilesSection'
import type { Participant } from '@/pages/ChatPage'

interface RightContextRailProps {
  roomId: string | null
  participants: Record<string, Participant>
  /** Mobile drawer flag. Desktop ignores this and uses the layout
   *  context's ``collapsed`` flag instead. */
  open: boolean
  onClose: () => void
}

/**
 * Right-side context rail (#302). Default-collapsed sibling of the
 * left navigation Sidebar; mirrors the same mobile-drawer pattern
 * (``Sidebar.tsx:354-376``) so users carry one mental model.
 *
 * Sections rendered top-to-bottom:
 *   - Tasks (#266 / #302) — current room's tasks
 *   - Shared Files (#246) — current room's uploaded files
 *
 * The ``Goals`` section lands in #302 Phase 2 alongside the Goal
 * scheduler. The container is intentionally generic so adding a new
 * <GoalsSection/> above <TasksSection/> requires zero structural
 * changes here.
 */
export default function RightContextRail({
  roomId,
  participants,
  open,
  onClose,
}: RightContextRailProps) {
  const { collapsed } = useRightSidebarLayout()

  // ESC closes the mobile drawer. Desktop is handled by the toggle
  // button — there is no full-screen overlay state to escape from.
  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [open, onClose])

  if (!roomId) return null

  return (
    <>
      {/* Mobile backdrop. Same chrome as the left Sidebar's. */}
      {open && (
        <button
          type="button"
          aria-label="Close context rail"
          className="fixed inset-0 z-30 bg-black/25 backdrop-blur-[1px] md:hidden"
          onClick={onClose}
        />
      )}

      <aside
        data-testid="right-rail-root"
        aria-hidden={collapsed && !open ? true : undefined}
        aria-label="Room context rail"
        className={`
          fixed inset-y-0 right-0 z-40 flex h-full w-80 flex-col border-l border-[var(--color-border)] bg-[var(--color-surface-alt)]
          transform transition-all duration-200 ease-out
          ${open ? 'translate-x-0 shadow-deep' : 'translate-x-full'}
          ${collapsed
            ? 'md:translate-x-full md:w-0 md:overflow-hidden md:border-l-0'
            : 'md:static md:z-auto md:translate-x-0 md:w-80'}
        `}
      >
        <div className="flex h-12 items-center justify-between border-b border-[var(--color-border)] px-3">
          <h2 className="text-[12px] font-semibold uppercase tracking-wider text-[var(--color-foreground-muted)]">
            Context
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="md:hidden rounded-[var(--radius-sm)] p-1 text-[var(--color-foreground-muted)] hover:bg-black/5"
            aria-label="Close context rail"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <ScrollArea className="flex-1">
          <TasksSection roomId={roomId} participants={participants} />
          <FilesSection roomId={roomId} />
        </ScrollArea>
      </aside>
    </>
  )
}

import { useMemo, useRef } from 'react'
import { useModalDrawer } from '@/hooks/useModalDrawer'
import { X } from 'lucide-react'
import { ScrollArea } from '@/components/ui/scroll-area'
import { useRightSidebarLayout } from '@/hooks/useRightSidebarLayout'
import TasksSection from '@/components/right-rail/TasksSection'
import FilesSection from '@/components/right-rail/FilesSection'
import GoalsSection from '@/components/right-rail/GoalsSection'
import type { Participant } from '@/pages/ChatPage'
import { useLocale } from '@/i18n/LocaleProvider'

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
 *   - Goals (#302 Phase 3) — recurring responsibilities reporting here
 *   - Tasks (#266 / #302)  — current room's tasks (manual + scheduled)
 *   - Shared Files (#246)  — current room's uploaded files
 *
 * The "Responsibilities" section is at the top because it's the most
 * proactive — it shapes what the agents *will* do, while Tasks shows
 * what they *are* doing and Files shows what's available.
 */
export default function RightContextRail({
  roomId,
  participants,
  open,
  onClose,
}: RightContextRailProps) {
  const { t } = useLocale()
  const { collapsed } = useRightSidebarLayout()
  const panelRef = useRef<HTMLElement>(null)
  const { desktop, active: drawerActive } = useModalDrawer({ open: open && !!roomId, onClose, panelRef, desktopMinWidth: 1024 })
  const hidden = desktop ? collapsed : !open

  // #312 — pass full agent participants (not just ids) to
  // ``GoalsSection`` so the form can render an explicit picker and
  // rows can resolve agent names locally without an extra fetch.
  const agentParticipants = useMemo(
    () =>
      Object.values(participants).filter(
        (p) => p.kind === 'agent' && p.agent_id,
      ),
    [participants],
  )

  if (!roomId) return null

  return (
    <>
      {/* Mobile backdrop. Same chrome as the left Sidebar's. */}
      {open && (
        <button
          type="button"
          aria-label={t('chat.closeContext')}
          data-drawer-overlay="room-context-rail"
          tabIndex={-1}
          className="fixed inset-0 z-30 bg-black/25 backdrop-blur-[1px] lg:hidden"
          onClick={onClose}
        />
      )}

      <aside
        ref={panelRef}
        id="room-context-rail"
        data-navigation-drawer
        role={drawerActive ? 'dialog' : undefined}
        aria-modal={drawerActive || undefined}
        tabIndex={-1}
        data-testid="right-rail-root"
        aria-hidden={hidden ? true : undefined}
        inert={hidden}
        aria-label={t('chat.contextRail')}
        // #329 — width is staged across breakpoints so the rail no
        // longer eats a fixed 384px below xl: w-72 (288px) on the
        // mobile drawer and md desktop, w-80 (320px) on lg, full
        // w-96 (384px) only at xl+.
        className={`
          fixed inset-y-0 right-0 z-40 flex h-full min-w-0 w-72 flex-col border-l border-[var(--color-border)] bg-[var(--color-surface-alt)]
          transform transition-all duration-200 ease-out
          ${open ? 'translate-x-0 shadow-deep' : 'translate-x-full'}
          ${collapsed
            ? 'lg:translate-x-full lg:w-0 lg:overflow-hidden lg:border-l-0'
            : 'lg:static lg:z-auto lg:translate-x-0 lg:w-80'}
        `}
      >
        <div className="flex h-14 shrink-0 items-center justify-between border-b border-[var(--color-border)] px-3">
          <h2 className="text-sm font-semibold text-[var(--color-foreground-muted)]">
            {t('chat.context')}
          </h2>
          <button
            type="button"
            onClick={onClose}
            data-drawer-close
            className="flex h-11 w-11 items-center justify-center rounded-[var(--radius-sm)] text-[var(--color-foreground-muted)] hover:bg-[var(--color-surface-hover)] lg:hidden"
            aria-label={t('chat.closeContext')}
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <ScrollArea className="min-w-0 flex-1">
          <GoalsSection
            roomId={roomId}
            agentParticipants={agentParticipants}
          />
          <TasksSection roomId={roomId} participants={participants} />
          <FilesSection roomId={roomId} />
        </ScrollArea>
      </aside>
    </>
  )
}

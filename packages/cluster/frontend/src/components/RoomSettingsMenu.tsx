import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Button } from '@/components/ui/button'
import { Activity, FolderOpen, FolderPlus, Image as ImageIcon, Link2, MoreHorizontal, PanelRight, OctagonX, Search, Settings, Trash2, UserPlus } from 'lucide-react'
import { useLocale } from '@/i18n/LocaleProvider'

/**
 * Overflow menu that groups the room's admin-scoped actions into a
 * single ``…`` trigger.
 *
 * The header used to display Sub-room / Edit / Invites / Agents /
 * Stop All as parallel inline buttons. That scaled poorly — on
 * admin rooms with every handler wired up, the control strip ran
 * into the participant count and the connected badge. Collapsing
 * the mutation actions here keeps the "room state" glance
 * information visible while still exposing every admin capability
 * behind one predictable entry point.
 *
 * Callers pass action handlers in; each one is only rendered when
 * its handler is provided, matching the previous "show when
 * permitted" semantics. Dangerous actions (``onStopAllAgents``)
 * are rendered in a distinct destructive row at the bottom with a
 * separator so destructive actions remain distinct.
 */

export interface RoomSettingsMenuProps {
  onCreateSubRoom?: () => void
  onEditRoom?: () => void
  onManageInvites?: () => void
  onManageAgents?: () => void
  onManageWorkspaces?: () => void
  /** #329 Phase 4 — search trigger. Mirrors the header's direct
   *  search button for narrow viewports where the icon is hidden
   *  (sub-sm). Mobile users can't type ⌘K, so they need a menu
   *  fallback. */
  onSearch?: () => void
  threadDisplayMode?: 'panel' | 'inline'
  onToggleThreadDisplayMode?: () => void
  /** #329 Phase 3 — agent-produced artifacts viewer. Available to
   *  every room member (no admin gate); kept here in the overflow
   *  menu so the header strip doesn't grow another inline icon. */
  onShowArtifacts?: () => void
  /** #429 — admin-only room activity / multi-agent flow viewer. Gated
   *  by the caller (passed only when ``user.is_admin``); the endpoint is
   *  admin-only too. */
  onShowRoomActivity?: () => void
  onStopAllAgents?: () => void
  onDeleteRoom?: () => void
}

export default function RoomSettingsMenu({
  onCreateSubRoom,
  onEditRoom,
  onManageInvites,
  onManageAgents,
  onManageWorkspaces,
  onSearch,
  threadDisplayMode,
  onToggleThreadDisplayMode,
  onShowArtifacts,
  onShowRoomActivity,
  onStopAllAgents,
  onDeleteRoom,
}: RoomSettingsMenuProps) {
  const { t } = useLocale()
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)

  const safeActions = [
    threadDisplayMode && onToggleThreadDisplayMode && {
      label: t('chat.threadModeAction', {
        mode: t(threadDisplayMode === 'panel' ? 'chat.threadModePanel' : 'chat.threadModeInline'),
        next: t(threadDisplayMode === 'panel' ? 'chat.threadModeInline' : 'chat.threadModePanel'),
      }),
      icon: <PanelRight className="h-4 w-4" />,
      onClick: onToggleThreadDisplayMode,
      testId: 'room-menu-thread-mode',
    },
    onSearch && {
      label: t('chat.searchMessages'),
      icon: <Search className="h-4 w-4" />,
      onClick: onSearch,
      testId: 'room-menu-search',
    },
    onCreateSubRoom && {
      label: t('chat.createSubRoom'),
      icon: <FolderPlus className="h-4 w-4" />,
      onClick: onCreateSubRoom,
      testId: 'room-menu-new-sub-room',
    },
    onEditRoom && {
      label: t('chat.editRoom'),
      icon: <Settings className="h-4 w-4" />,
      onClick: onEditRoom,
      testId: 'room-menu-edit',
    },
    onManageInvites && {
      label: t('chat.inviteLinks'),
      icon: <Link2 className="h-4 w-4" />,
      onClick: onManageInvites,
      testId: 'room-menu-invites',
    },
    onManageAgents && {
      label: t('chat.manageAgents'),
      icon: <UserPlus className="h-4 w-4" />,
      onClick: onManageAgents,
      testId: 'room-menu-agents',
    },
    onManageWorkspaces && {
      label: t('workspace.roomTitle'),
      icon: <FolderOpen className="h-4 w-4" />,
      onClick: onManageWorkspaces,
      testId: 'room-menu-workspaces',
    },
    onShowArtifacts && {
      label: t('chat.artifacts'),
      icon: <ImageIcon className="h-4 w-4" />,
      onClick: onShowArtifacts,
      testId: 'room-menu-artifacts',
    },
    onShowRoomActivity && {
      label: t('chat.roomActivity'),
      icon: <Activity className="h-4 w-4" />,
      onClick: onShowRoomActivity,
      testId: 'room-menu-activity',
    },
  ].filter(Boolean) as {
    label: string
    icon: ReactNode
    onClick: () => void
    testId?: string
  }[]

  // NOTE: hooks run BEFORE any conditional return so the call order
  // stays stable when a parent swings a handler prop in/out (e.g.
  // permissions resolve asynchronously and ``onStopAllAgents``
  // toggles from undefined → function between renders). Returning
  // null higher up would violate React's rules-of-hooks invariant.
  useEffect(() => {
    if (!open) return
    const onOutside = (e: Event) => {
      if (!rootRef.current) return
      if (!rootRef.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    // ``pointerdown`` covers both mouse and touch in one handler,
    // where ``mousedown`` alone missed iOS Safari taps and produced
    // a sticky open state on mobile.
    document.addEventListener('pointerdown', onOutside)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', onOutside)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  // No handlers at all → render nothing, mirroring the old
  // "button only appears when its callback exists" behavior.
  if (
    safeActions.length === 0 &&
    !onStopAllAgents &&
    !onDeleteRoom
  )
    return null

  const handleSelect = (run: () => void) => {
    setOpen(false)
    run()
  }

  return (
    <div ref={rootRef} className="relative">
      <Button
        variant="ghost"
        size="icon"
        onClick={() => setOpen((v) => !v)}
        title={t('chat.roomSettings')}
        aria-label={t('chat.roomSettings')}
        // Paired with ``role="group"`` on the flyout — ``dialog``
        // is the honest haspopup value when we aren't implementing
        // full menu-role semantics.
        aria-haspopup="dialog"
        aria-expanded={open}
        data-testid="room-header-settings-menu-trigger"
      >
        <MoreHorizontal className="h-4 w-4" />
      </Button>
      {open && (
        <div
          // ``role="group"`` instead of ``role="menu"``: ARIA
          // APG's menu role implies arrow-key navigation between
          // items, which we don't implement. The group role makes
          // no such promise — screen readers announce the labelled
          // group and the button children stay naturally Tab-able.
          role="group"
          aria-label={t('chat.roomSettings')}
          className="absolute right-0 top-full z-40 mt-1 w-64 max-w-[calc(100vw-2rem)] max-h-[70dvh] overflow-y-auto rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-surface)] shadow-lg"
        >
          <ul className="py-1">
            {safeActions.map((a) => (
              <li key={a.label}>
                <button
                  type="button"
                  onClick={() => handleSelect(a.onClick)}
                  data-testid={a.testId}
                  className="flex min-h-[var(--control-height)] w-full items-center gap-2 px-3 py-1.5 text-left text-sm text-[var(--color-foreground)] hover:bg-[var(--color-surface-hover)] cursor-pointer"
                >
                  {a.icon}
                  <span>{a.label}</span>
                </button>
              </li>
            ))}
            {(onStopAllAgents || onDeleteRoom) && safeActions.length > 0 && (
              <li
                aria-hidden="true"
                className="my-1 border-t border-[var(--color-border)]"
              />
            )}
            {onStopAllAgents && (
              <li>
                <button
                  type="button"
                  onClick={() => handleSelect(onStopAllAgents)}
                  data-testid="room-menu-stop-all"
                  // Destructive row — red text makes the consequence
                  // obvious. The divider above further separates it
                  // from the safe-action group so a stray click is
                  // less likely.
                  className="flex min-h-[var(--control-height)] w-full items-center gap-2 px-3 py-1.5 text-left text-sm text-[var(--color-destructive)] hover:bg-[var(--color-destructive)]/10 cursor-pointer"
                >
                  <OctagonX className="h-4 w-4" />
                  <span>{t('chat.stopAllAgents')}</span>
                </button>
              </li>
            )}
            {onDeleteRoom && (
              <li>
                <button
                  type="button"
                  onClick={() => handleSelect(onDeleteRoom)}
                  data-testid="room-menu-delete"
                  // Sits in the same destructive group as Stop All —
                  // shares red styling. Caller is expected to gate
                  // this prop on the same admin/owner check the
                  // server enforces, and to prompt for confirmation
                  // before actually firing the DELETE.
                  className="flex min-h-[var(--control-height)] w-full items-center gap-2 px-3 py-1.5 text-left text-sm text-[var(--color-destructive)] hover:bg-[var(--color-destructive)]/10 cursor-pointer"
                >
                  <Trash2 className="h-4 w-4" />
                  <span>{t('chat.deleteRoom')}</span>
                </button>
              </li>
            )}
          </ul>
        </div>
      )}
    </div>
  )
}

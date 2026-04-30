import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Button } from '@/components/ui/button'
import { FolderPlus, Image as ImageIcon, Link2, MoreHorizontal, OctagonX, Settings, Trash2, UserPlus } from 'lucide-react'

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
 * separator, per the Slack-style chat-UX convention.
 */

export interface RoomSettingsMenuProps {
  onCreateSubRoom?: () => void
  onEditRoom?: () => void
  onManageInvites?: () => void
  onManageAgents?: () => void
  /** #329 Phase 3 — agent-produced artifacts viewer. Available to
   *  every room member (no admin gate); kept here in the overflow
   *  menu so the header strip doesn't grow another inline icon. */
  onShowArtifacts?: () => void
  onStopAllAgents?: () => void
  onDeleteRoom?: () => void
}

export default function RoomSettingsMenu({
  onCreateSubRoom,
  onEditRoom,
  onManageInvites,
  onManageAgents,
  onShowArtifacts,
  onStopAllAgents,
  onDeleteRoom,
}: RoomSettingsMenuProps) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)

  const safeActions = [
    onCreateSubRoom && {
      label: 'Create sub-room',
      icon: <FolderPlus className="h-4 w-4" />,
      onClick: onCreateSubRoom,
      testId: 'room-menu-new-sub-room',
    },
    onEditRoom && {
      label: 'Edit room',
      icon: <Settings className="h-4 w-4" />,
      onClick: onEditRoom,
      testId: 'room-menu-edit',
    },
    onManageInvites && {
      label: 'Invite links',
      icon: <Link2 className="h-4 w-4" />,
      onClick: onManageInvites,
      testId: 'room-menu-invites',
    },
    onManageAgents && {
      label: 'Manage agents',
      icon: <UserPlus className="h-4 w-4" />,
      onClick: onManageAgents,
      testId: 'room-menu-agents',
    },
    onShowArtifacts && {
      label: 'Artifacts',
      icon: <ImageIcon className="h-4 w-4" />,
      onClick: onShowArtifacts,
      testId: 'room-menu-artifacts',
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
        size="sm"
        onClick={() => setOpen((v) => !v)}
        title="Room settings"
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
          aria-label="Room settings"
          className="absolute right-0 top-9 z-40 w-52 overflow-hidden rounded-[var(--radius-md)] border border-[var(--color-border)] bg-white shadow-lg"
        >
          <ul className="py-1">
            {safeActions.map((a) => (
              <li key={a.label}>
                <button
                  type="button"
                  onClick={() => handleSelect(a.onClick)}
                  data-testid={a.testId}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-[var(--color-foreground)] hover:bg-black/5 cursor-pointer"
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
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-red-600 hover:bg-red-50 cursor-pointer"
                >
                  <OctagonX className="h-4 w-4" />
                  <span>Stop all agents</span>
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
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-red-600 hover:bg-red-50 cursor-pointer"
                >
                  <Trash2 className="h-4 w-4" />
                  <span>Delete room</span>
                </button>
              </li>
            )}
          </ul>
        </div>
      )}
    </div>
  )
}

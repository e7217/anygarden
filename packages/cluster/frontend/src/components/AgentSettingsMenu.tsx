import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Button } from '@/components/ui/button'
import {
  Check,
  Copy,
  DoorOpen,
  EyeOff,
  FileCog,
  History,
  MoreHorizontal,
  Smile,
  Trash2,
} from 'lucide-react'

/**
 * AgentSettingsMenu — Issue #101.
 *
 * Mirrors ``RoomSettingsMenu`` for per-agent admin actions, and
 * replaces the four inline icon buttons
 * (Manage rooms / Edit manifest / Activity / Delete) that the
 * AdminMachines row used to stack. The Slack-style "collapse
 * mutation actions behind a single ⋯ trigger" pattern that
 * RoomSettingsMenu introduced solves the same row-crowding problem
 * here: the admin's per-row glance surface (name, avatar, state,
 * engine) stays legible, and every admin capability lives behind
 * one predictable entry point.
 *
 * What stays inline in AdminMachines (and therefore does NOT have a
 * corresponding prop here) is Start/Stop — it's a frequent toggle
 * and the Play/Square icons communicate state in a way a menu
 * entry would lose.
 *
 * Props are optional per action: a menu item only renders when its
 * handler is supplied, preserving the "show-when-permitted"
 * semantics that RoomSettingsMenu locked in.
 */
export interface AgentSettingsMenuProps {
  onEditAvatar?: () => void
  onEditManifest?: () => void
  onManageRooms?: () => void
  onShowActivity?: () => void
  onCopyId?: () => void
  onDelete?: () => void
  /** #148 Part 2 — current value of the opt-out flag. When provided
   *  with ``onToggleContextWindowOptOut``, the menu renders a
   *  check-mark-style toggle item so admins can flip the flag
   *  inline (no separate dialog). The two props are a pair: if
   *  either is omitted the item is not rendered. */
  contextWindowOptOut?: boolean
  onToggleContextWindowOptOut?: () => void | Promise<void>
}

export default function AgentSettingsMenu({
  onEditAvatar,
  onEditManifest,
  onManageRooms,
  onShowActivity,
  onCopyId,
  onDelete,
  contextWindowOptOut,
  onToggleContextWindowOptOut,
}: AgentSettingsMenuProps) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)

  const safeActions = [
    onEditAvatar && {
      label: 'Edit avatar',
      icon: <Smile className="h-4 w-4" />,
      onClick: onEditAvatar,
      testId: 'agent-menu-edit-avatar',
    },
    onEditManifest && {
      label: 'Edit manifest',
      icon: <FileCog className="h-4 w-4" />,
      onClick: onEditManifest,
      testId: 'agent-menu-edit-manifest',
    },
    onManageRooms && {
      label: 'Manage rooms',
      icon: <DoorOpen className="h-4 w-4" />,
      onClick: onManageRooms,
      testId: 'agent-menu-manage-rooms',
    },
    onShowActivity && {
      label: 'Activity',
      icon: <History className="h-4 w-4" />,
      onClick: onShowActivity,
      testId: 'agent-menu-activity',
    },
    onCopyId && {
      label: 'Copy agent ID',
      icon: <Copy className="h-4 w-4" />,
      onClick: onCopyId,
      testId: 'agent-menu-copy-id',
    },
  ].filter(Boolean) as {
    label: string
    icon: ReactNode
    onClick: () => void
    testId?: string
  }[]

  // Hooks before any conditional return — see RoomSettingsMenu
  // for the rules-of-hooks rationale; the same prop-swings-async
  // concern applies here (permission checks toggling handler
  // props between renders).
  useEffect(() => {
    if (!open) return
    const onOutside = (e: Event) => {
      if (!rootRef.current) return
      if (!rootRef.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', onOutside)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', onOutside)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const showContextToggle =
    typeof contextWindowOptOut === 'boolean' &&
    typeof onToggleContextWindowOptOut === 'function'

  if (safeActions.length === 0 && !onDelete && !showContextToggle) return null

  const handleSelect = (run: () => void | Promise<void>) => {
    setOpen(false)
    void run()
  }

  return (
    <div ref={rootRef} className="relative">
      <Button
        variant="ghost"
        size="icon"
        onClick={() => setOpen(v => !v)}
        title="Agent settings"
        aria-haspopup="dialog"
        aria-expanded={open}
        data-testid="agent-settings-menu-trigger"
      >
        <MoreHorizontal className="h-4 w-4" />
      </Button>
      {open && (
        <div
          role="group"
          aria-label="Agent settings"
          className="absolute right-0 top-9 z-40 w-52 overflow-hidden rounded-[var(--radius-md)] border border-[var(--color-border)] bg-white shadow-lg"
        >
          <ul className="py-1">
            {safeActions.map(a => (
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
            {showContextToggle && (
              <>
                {safeActions.length > 0 && (
                  <li
                    aria-hidden="true"
                    className="my-1 border-t border-[var(--color-border)]"
                  />
                )}
                <li>
                  {/* #148 Part 2 — toggle-style menu item. The trailing
                      check mark reflects the current DB flag; the
                      button only fires the mutation callback so the
                      parent owns optimistic state + rollback. */}
                  <button
                    type="button"
                    role="menuitemcheckbox"
                    aria-checked={contextWindowOptOut}
                    onClick={() =>
                      handleSelect(onToggleContextWindowOptOut!)
                    }
                    data-testid="agent-menu-context-window-opt-out"
                    className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-[var(--color-foreground)] hover:bg-black/5 cursor-pointer"
                  >
                    <EyeOff className="h-4 w-4" />
                    <span className="flex-1">대화 맥락 공유 제외</span>
                    {contextWindowOptOut ? (
                      <Check
                        className="h-4 w-4 text-[var(--color-brand)]"
                        aria-hidden="true"
                      />
                    ) : null}
                  </button>
                </li>
              </>
            )}
            {onDelete && (safeActions.length > 0 || showContextToggle) && (
              <li
                aria-hidden="true"
                className="my-1 border-t border-[var(--color-border)]"
              />
            )}
            {onDelete && (
              <li>
                <button
                  type="button"
                  onClick={() => handleSelect(onDelete)}
                  data-testid="agent-menu-delete"
                  // Destructive row — red text and separator above
                  // make the consequence obvious, matching
                  // RoomSettingsMenu's "Delete room" convention.
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-red-600 hover:bg-red-50 cursor-pointer"
                >
                  <Trash2 className="h-4 w-4" />
                  <span>Delete agent</span>
                </button>
              </li>
            )}
          </ul>
        </div>
      )}
    </div>
  )
}

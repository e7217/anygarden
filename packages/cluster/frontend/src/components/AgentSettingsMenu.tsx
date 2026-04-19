import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import {
  Check,
  EyeOff,
  MoreHorizontal,
  Settings,
  Trash2,
} from 'lucide-react'

/**
 * AgentSettingsMenu — collapsed agent row menu (#101, rewired in #158).
 *
 * Per-agent admin actions that don't fit inline on the row live
 * behind a single ⋯ trigger. After the #158 unification, the menu
 * collapses to three items:
 *
 * - **Settings…** — opens the unified AgentSettingsDialog
 *   (Overview / Manifest / Rooms / Activity). Replaces the five
 *   individual items (Edit avatar, Edit manifest, Manage rooms,
 *   Activity, Copy agent ID) that used to fan out.
 * - **대화 맥락 공유 제외** — one-click toggle kept in the menu
 *   because its trailing check-mark communicates state efficiently.
 * - **Delete agent** — destructive; stays here for the same reason
 *   every other row-menu keeps its Delete at the bottom with a red
 *   separator.
 *
 * Each prop is optional: a menu item only renders when its handler
 * is supplied, preserving the "show-when-permitted" semantics.
 */
export interface AgentSettingsMenuProps {
  /** Opens the unified AgentSettingsDialog. When omitted, the
   *  Settings… entry is hidden (useful for callers that don't mount
   *  the dialog). */
  onOpenSettings?: () => void
  onDelete?: () => void
  /** #148 Part 2 — current value of the opt-out flag. Paired with
   *  ``onToggleContextWindowOptOut``: the menu renders a check-mark
   *  toggle row when both are provided. */
  contextWindowOptOut?: boolean
  onToggleContextWindowOptOut?: () => void | Promise<void>
}

export default function AgentSettingsMenu({
  onOpenSettings,
  onDelete,
  contextWindowOptOut,
  onToggleContextWindowOptOut,
}: AgentSettingsMenuProps) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)

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

  if (!onOpenSettings && !onDelete && !showContextToggle) return null

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
            {onOpenSettings && (
              <li>
                <button
                  type="button"
                  onClick={() => handleSelect(onOpenSettings)}
                  data-testid="agent-menu-settings"
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-[var(--color-foreground)] hover:bg-black/5 cursor-pointer"
                >
                  <Settings className="h-4 w-4" />
                  <span>Settings…</span>
                </button>
              </li>
            )}
            {showContextToggle && (
              <>
                {onOpenSettings && (
                  <li
                    aria-hidden="true"
                    className="my-1 border-t border-[var(--color-border)]"
                  />
                )}
                <li>
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
            {onDelete && (onOpenSettings || showContextToggle) && (
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

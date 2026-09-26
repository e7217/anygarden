import { useRef, useState } from 'react'
import SidebarMenuPopover from '@/components/SidebarMenuPopover'
import { Button } from '@/components/ui/button'
import { useLocale } from '@/i18n/LocaleProvider'
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
  /** Sidebar rows use a 44px touch target on phones and a compact
   *  32px trigger beside the new-conversation button on desktop. */
  compact?: boolean
}

export default function AgentSettingsMenu({
  onOpenSettings,
  onDelete,
  contextWindowOptOut,
  onToggleContextWindowOptOut,
  compact = false,
}: AgentSettingsMenuProps) {
  const { t } = useLocale()
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)



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
      {compact ? (
        <button
          type="button"
          onClick={() => setOpen(v => !v)}
          title={t('admin.agentSettings.title')}
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-label={t('admin.agentSettings.title')}
          className="flex h-[var(--control-icon-size)] w-[var(--control-icon-size)] items-center justify-center rounded-[var(--radius-sm)] text-[var(--color-foreground-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-foreground)]"
          data-testid="agent-settings-menu-trigger"
        >
          <MoreHorizontal className="h-4 w-4" />
        </button>
      ) : (
        <Button
          variant="ghost"
          size="icon"
          onClick={() => setOpen(v => !v)}
          title={t('admin.agentSettings.title')}
          aria-haspopup="dialog"
          aria-expanded={open}
          data-testid="agent-settings-menu-trigger"
        >
          <MoreHorizontal className="h-4 w-4" />
        </Button>
      )}
      {open && (
        <SidebarMenuPopover
          anchorRef={rootRef}
          label={t('admin.agentSettings.title')}
          onClose={() => setOpen(false)}
        >
          <ul className="py-1">
            {onOpenSettings && (
              <li>
                <button
                  type="button"
                  onClick={() => handleSelect(onOpenSettings)}
                  data-testid="agent-menu-settings"
                  className="flex min-h-[var(--control-height)] w-full items-center gap-2 px-3 py-2 text-left text-sm text-[var(--color-foreground)] hover:bg-[var(--color-surface-hover)] cursor-pointer"
                >
                  <Settings className="h-4 w-4" />
                  <span>{t('admin.agentSettings.settingsAction')}</span>
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
                    className="flex min-h-[var(--control-height)] w-full items-center gap-2 px-3 py-2 text-left text-sm text-[var(--color-foreground)] hover:bg-[var(--color-surface-hover)] cursor-pointer"
                  >
                    <EyeOff className="h-4 w-4" />
                    <span className="flex-1">{t('admin.agentSettings.contextOptOut')}</span>
                    {contextWindowOptOut ? (
                      <Check
                        className="h-4 w-4 text-[var(--color-brand-text)]"
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
                  className="flex min-h-[var(--control-height)] w-full items-center gap-2 px-3 py-2 text-left text-sm text-[var(--color-destructive)] hover:bg-[var(--color-destructive)]/10 cursor-pointer"
                >
                  <Trash2 className="h-4 w-4" />
                  <span>{t('admin.agentSettings.deleteAgent')}</span>
                </button>
              </li>
            )}
          </ul>
        </SidebarMenuPopover>
      )}
    </div>
  )
}

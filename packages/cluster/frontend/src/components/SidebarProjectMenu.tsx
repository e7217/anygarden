import { useRef, useState } from 'react'
import SidebarMenuPopover from '@/components/SidebarMenuPopover'
import { ArrowDown, ArrowUp, MoreHorizontal, Trash2 } from 'lucide-react'
import { useLocale } from '@/i18n/LocaleProvider'

/**
 * Hover-revealed overflow menu for a sidebar project row.
 *
 * Mirrors ``SidebarRoomMenu`` — see that file for the rationale on
 * keeping these narrow menus separate from ``RoomSettingsMenu``.
 * The project row's menu moves the project up or down in the
 * caller's own sidebar order (#995) and, for callers who may, deletes
 * it. Rename is not currently implemented (issue #77 is delete-only).
 * Each action is omitted when its handler is not passed.
 *
 * The parent project row uses the ``group`` / ``group-hover``
 * pattern so the trigger fades in on hover at md+. On mobile the
 * trigger stays visible — without it, a touch user's first tap
 * would toggle the project's expand/collapse state instead of
 * opening the menu.
 *
 * Click propagation is stopped on every interactive element so
 * the outer row's toggle handler never fires while the user is
 * interacting with the menu.
 */

export interface SidebarProjectMenuProps {
  projectId: string
  /** Omitted when the caller may not delete the project (#783). */
  onDelete?: () => void
  /** Omitted when there is nothing to reorder; null when the project
   *  is already at that end of the list (#995). */
  onMoveUp?: (() => void) | null
  onMoveDown?: (() => void) | null
}

const itemClass = 'flex min-h-[var(--control-height)] w-full items-center gap-2 px-3 text-left text-sm text-[var(--color-foreground)] hover:bg-[var(--color-surface-hover)] cursor-pointer disabled:cursor-not-allowed disabled:text-[var(--color-foreground-subtle)] disabled:hover:bg-transparent'

export default function SidebarProjectMenu({ projectId, onDelete, onMoveUp, onMoveDown }: SidebarProjectMenuProps) {
  const { t } = useLocale()
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)



  const pick = (run: () => void) => (e: React.MouseEvent) => {
    e.stopPropagation()
    setOpen(false)
    run()
  }

  return (
    <div
      ref={rootRef}
      className={`
        ml-1 shrink-0
        opacity-100 md:pointer-fine:opacity-0 md:group-hover:opacity-100 md:group-focus-within:opacity-100
        ${open ? 'md:opacity-100' : ''}
        transition-opacity
      `}
      onClick={(e) => e.stopPropagation()}
    >
      <button
        type="button"
        onPointerDown={(e) => e.stopPropagation()}
        onClick={(e) => {
          e.stopPropagation()
          setOpen((v) => !v)
        }}
        className="flex h-[var(--control-icon-size)] w-[var(--control-icon-size)] items-center justify-center rounded-[var(--radius-sm)] text-[var(--color-foreground-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-foreground)]"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={t('chat.projectActions')}
        title={t('chat.projectActions')}
        data-testid={`sidebar-project-menu-${projectId}`}
      >
        <MoreHorizontal className="h-4 w-4" />
      </button>
      {open && (
        <SidebarMenuPopover
          anchorRef={rootRef}
          label={t('chat.projectActions')}
          onClose={() => setOpen(false)}
          width="narrow"
        >
          <ul className="py-1">
            {onMoveUp !== undefined && (
              <li>
                <button
                  type="button"
                  disabled={onMoveUp === null}
                  onClick={onMoveUp ? pick(onMoveUp) : undefined}
                  data-testid={`sidebar-project-menu-up-${projectId}`}
                  className={itemClass}
                >
                  <ArrowUp className="h-4 w-4" />
                  <span>{t('chat.moveProjectUp')}</span>
                </button>
              </li>
            )}
            {onMoveDown !== undefined && (
              <li>
                <button
                  type="button"
                  disabled={onMoveDown === null}
                  onClick={onMoveDown ? pick(onMoveDown) : undefined}
                  data-testid={`sidebar-project-menu-down-${projectId}`}
                  className={itemClass}
                >
                  <ArrowDown className="h-4 w-4" />
                  <span>{t('chat.moveProjectDown')}</span>
                </button>
              </li>
            )}
            {onDelete && (
              <li>
                <button
                  type="button"
                  onClick={pick(onDelete)}
                  data-testid={`sidebar-project-menu-delete-${projectId}`}
                  className="flex min-h-[var(--control-height)] w-full items-center gap-2 px-3 text-left text-sm text-[var(--color-destructive)] hover:bg-[var(--color-destructive)]/10 cursor-pointer"
                >
                  <Trash2 className="h-4 w-4" />
                  <span>{t('chat.deleteProject')}</span>
                </button>
              </li>
            )}
          </ul>
        </SidebarMenuPopover>
      )}
    </div>
  )
}

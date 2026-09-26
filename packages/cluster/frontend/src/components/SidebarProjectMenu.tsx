import { useRef, useState } from 'react'
import SidebarMenuPopover from '@/components/SidebarMenuPopover'
import { MoreHorizontal, Trash2 } from 'lucide-react'
import { useLocale } from '@/i18n/LocaleProvider'

/**
 * Hover-revealed overflow menu for a sidebar project row.
 *
 * Mirrors ``SidebarRoomMenu`` — see that file for the rationale on
 * keeping these narrow menus separate from ``RoomSettingsMenu``.
 * The project row's menu exposes only "Delete project"; rename is
 * not currently implemented (issue #77 is delete-only).
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
  onDelete: () => void
}

export default function SidebarProjectMenu({ projectId, onDelete }: SidebarProjectMenuProps) {
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
          </ul>
        </SidebarMenuPopover>
      )}
    </div>
  )
}

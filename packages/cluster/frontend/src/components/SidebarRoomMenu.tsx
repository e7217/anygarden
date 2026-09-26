import { useRef, useState } from 'react'
import SidebarMenuPopover from '@/components/SidebarMenuPopover'
import { MoreHorizontal, Pencil, Trash2 } from 'lucide-react'
import { useLocale } from '@/i18n/LocaleProvider'

/**
 * Hover-revealed overflow menu for a sidebar room row.
 *
 * Sits inside ``RoomTreeNodeView`` on the right edge of the row.
 * The parent row uses the ``group`` / ``group-hover`` pattern used
 * elsewhere in the codebase (``TaskPanel``, ``MessageBubble``) so
 * the trigger fades in on hover without reserving horizontal space
 * at rest.
 *
 * Why a dedicated component (vs. reusing ``RoomSettingsMenu``):
 * the sidebar row is narrow, exposes only two actions, and needs
 * the hover-reveal behavior — ``RoomSettingsMenu`` is a wider
 * 5+ action menu designed for the room header. Keeping this one
 * small prevents its layout assumptions from bleeding into the
 * sidebar context.
 *
 * Click-on-trigger and clicks inside the popover must NOT
 * propagate to the row's own click handler (which navigates to
 * the room), so every interactive element stops propagation.
 * ``pointerdown`` on the trigger also stops propagation to avoid
 * the outer row capturing the press before the click lands.
 */

export interface SidebarRoomMenuProps {
  roomId: string
  onRename: () => void
  onDelete: () => void
}

export default function SidebarRoomMenu({ roomId, onRename, onDelete }: SidebarRoomMenuProps) {
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
      // md+ 에서는 hover 시에만 보이고, 모바일(터치)에서는
      // 항상 노출한다. 터치 환경에서 group-hover는 첫 탭이
      // 바로 방 이동으로 연결되므로 버튼을 보이지 않게 두면
      // 접근 자체가 불가능하다.
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
        aria-label={t('chat.roomActions')}
        title={t('chat.roomActions')}
        data-testid={`sidebar-room-menu-${roomId}`}
      >
        <MoreHorizontal className="h-4 w-4" />
      </button>
      {open && (
        <SidebarMenuPopover
          anchorRef={rootRef}
          label={t('chat.roomActions')}
          onClose={() => setOpen(false)}
          width="narrow"
        >
          <ul className="py-1">
            <li>
              <button
                type="button"
                onClick={pick(onRename)}
                data-testid={`sidebar-room-menu-rename-${roomId}`}
                className="flex min-h-[var(--control-height)] w-full items-center gap-2 px-3 text-left text-sm text-[var(--color-foreground)] hover:bg-[var(--color-surface-hover)] cursor-pointer"
              >
                <Pencil className="h-4 w-4" />
                <span>{t('chat.rename')}</span>
              </button>
            </li>
            <li>
              <button
                type="button"
                onClick={pick(onDelete)}
                data-testid={`sidebar-room-menu-delete-${roomId}`}
                // Destructive — red text matches RoomSettingsMenu's
                // delete row styling so the consequence is visually
                // consistent across the two entry points.
                className="flex min-h-[var(--control-height)] w-full items-center gap-2 px-3 text-left text-sm text-[var(--color-destructive)] hover:bg-[var(--color-destructive)]/10 cursor-pointer"
              >
                <Trash2 className="h-4 w-4" />
                <span>{t('chat.deleteRoom')}</span>
              </button>
            </li>
          </ul>
        </SidebarMenuPopover>
      )}
    </div>
  )
}

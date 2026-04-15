import { useEffect, useRef, useState } from 'react'
import { MoreHorizontal, Pencil, Trash2 } from 'lucide-react'

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
        opacity-100 md:opacity-0 md:group-hover:opacity-100
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
        className="flex h-6 w-6 items-center justify-center rounded-[var(--radius-sm)] text-[var(--color-foreground-muted)] hover:bg-black/10 hover:text-[var(--color-foreground)]"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label="Room actions"
        title="Room actions"
        data-testid={`sidebar-room-menu-${roomId}`}
      >
        <MoreHorizontal className="h-4 w-4" />
      </button>
      {open && (
        <div
          role="group"
          aria-label="Room actions"
          className="absolute right-2 z-40 mt-1 w-40 overflow-hidden rounded-[var(--radius-md)] border border-[var(--color-border)] bg-white shadow-lg"
        >
          <ul className="py-1">
            <li>
              <button
                type="button"
                onClick={pick(onRename)}
                data-testid={`sidebar-room-menu-rename-${roomId}`}
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-[var(--color-foreground)] hover:bg-black/5 cursor-pointer"
              >
                <Pencil className="h-4 w-4" />
                <span>Rename</span>
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
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-red-600 hover:bg-red-50 cursor-pointer"
              >
                <Trash2 className="h-4 w-4" />
                <span>Delete room</span>
              </button>
            </li>
          </ul>
        </div>
      )}
    </div>
  )
}

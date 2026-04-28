import { PanelRightOpen, PanelRightClose } from 'lucide-react'
import { useRightSidebarLayout } from '@/hooks/useRightSidebarLayout'
import { useRightRailNotice } from '@/hooks/useRightRailNotice'

interface RightRailToggleProps {
  /** Active room — drives the notice signal. ``null`` when the host
   *  hasn't selected one (no rail to open). */
  roomId: string | null
  /** Mobile drawer hook. Desktop uses the layout context directly;
   *  mobile passes a separate handler so the host can manage the
   *  ``open`` overlay state. */
  onMobileOpen?: () => void
}

/**
 * Toggle button for the right context rail (#302). Mounted in the
 * RoomHeader's right-side actions. Renders a small notion-blue dot
 * when there is unread context activity (new tasks/files arriving
 * while the rail is closed). The dot is intentionally a single
 * boolean — not a counter — to fit the design's "feels-it" aesthetic.
 */
export default function RightRailToggle({ roomId, onMobileOpen }: RightRailToggleProps) {
  const { collapsed, toggleCollapsed } = useRightSidebarLayout()
  const hasNotice = useRightRailNotice(roomId)

  const handleClick = () => {
    // Mobile: ask the host to open the overlay drawer. Desktop: flip
    // the persisted layout flag. The split mirrors how Sidebar.tsx
    // separates ``open`` (mobile drawer) from ``collapsed`` (desktop
    // layout).
    if (onMobileOpen) onMobileOpen()
    toggleCollapsed()
  }

  const Icon = collapsed ? PanelRightOpen : PanelRightClose
  return (
    <button
      type="button"
      onClick={handleClick}
      data-testid="right-rail-toggle"
      aria-label={collapsed ? 'Open context rail' : 'Close context rail'}
      title={collapsed ? 'Open context rail' : 'Close context rail'}
      className="relative inline-flex rounded-[var(--radius-sm)] p-1 text-[var(--color-foreground-muted)] hover:bg-black/5 hover:text-[var(--color-foreground)] transition-colors"
    >
      <Icon className="h-4 w-4" />
      {hasNotice && (
        <span
          aria-hidden="true"
          className="absolute top-1 right-1 h-1.5 w-1.5 rounded-full bg-[var(--color-brand)]"
          data-testid="right-rail-notice-dot"
        />
      )}
    </button>
  )
}

import { useEffect, useState } from 'react'
import { PanelRightOpen, PanelRightClose } from 'lucide-react'
import { useRightSidebarLayout } from '@/hooks/useRightSidebarLayout'
import { useRightRailNotice } from '@/hooks/useRightRailNotice'
import { useLocale } from '@/i18n/LocaleProvider'

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
 * RoomHeader's right-side actions. Renders a small teal status dot
 * when there is unread context activity (new tasks/files arriving
 * while the rail is closed). The dot is intentionally a single
 * boolean — not a counter — to fit the design's "feels-it" aesthetic.
 */
export default function RightRailToggle({ roomId, onMobileOpen }: RightRailToggleProps) {
  const { t } = useLocale()
  const { collapsed, toggleCollapsed } = useRightSidebarLayout()
  const hasNotice = useRightRailNotice(roomId)
  const [compact, setCompact] = useState(() =>
    typeof window.matchMedia === 'function' ? window.matchMedia('(max-width: 1023px)').matches : true,
  )

  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const query = window.matchMedia('(max-width: 1023px)')
    const update = () => setCompact(query.matches)
    query.addEventListener('change', update)
    return () => query.removeEventListener('change', update)
  }, [])

  const handleClick = () => {
    // Mobile: ask the host to open the overlay drawer. Desktop: flip
    // the persisted layout flag. The split mirrors how Sidebar.tsx
    // separates ``open`` (mobile drawer) from ``collapsed`` (desktop
    // layout).
    if (compact) onMobileOpen?.()
    else toggleCollapsed()
  }

  const isClosed = compact || collapsed
  const Icon = isClosed ? PanelRightOpen : PanelRightClose
  return (
    <button
      type="button"
      onClick={handleClick}
      data-testid="right-rail-toggle"
      aria-label={isClosed ? t('chat.openContext') : t('chat.closeContext')}
      title={isClosed ? t('chat.openContext') : t('chat.closeContext')}
      className="relative inline-flex h-11 w-11 items-center justify-center rounded-[var(--radius-sm)] text-[var(--color-foreground-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-foreground)] transition-colors"
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

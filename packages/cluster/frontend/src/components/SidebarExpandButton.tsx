import { PanelLeftOpen } from 'lucide-react'
import { useSidebarLayout } from '@/hooks/useSidebarLayout'

/**
 * Floating expand button shown only when the desktop sidebar is
 * collapsed (#106/#115). Lives next to ``<Sidebar>`` in each page so
 * the page owns the visual z-stack — swapping it for a page-specific
 * placement later is a one-line edit at the call site.
 *
 * Hidden below ``md:`` — mobile uses the RoomHeader hamburger or
 * empty-state menu for off-canvas drawer control instead.
 */
export default function SidebarExpandButton() {
  const { collapsed, toggleCollapsed } = useSidebarLayout()
  if (!collapsed) return null
  return (
    <button
      type="button"
      onClick={toggleCollapsed}
      aria-label="Expand sidebar"
      data-testid="sidebar-expand"
      title="Expand sidebar (⌘B)"
      className="hidden md:inline-flex fixed left-2 top-2 z-30 items-center justify-center rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-white p-1.5 text-[var(--color-foreground-muted)] shadow-whisper hover:bg-black/5 hover:text-[var(--color-foreground)] transition-colors"
    >
      <PanelLeftOpen className="h-4 w-4" />
    </button>
  )
}

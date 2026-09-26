import { PanelLeftOpen } from 'lucide-react'
import { useSidebarLayout } from '@/hooks/useSidebarLayout'
import { useLocale } from '@/i18n/LocaleProvider'

/** Reserve a narrow desktop rail so restoring navigation never covers page content. */
export default function SidebarExpandButton() {
  const { t } = useLocale()
  const { collapsed, toggleCollapsed } = useSidebarLayout()
  if (!collapsed) return null
  return (
    <div
      data-testid="sidebar-collapsed-rail"
      className="hidden w-[calc(var(--control-icon-size)+1.5rem)] shrink-0 border-r border-[var(--color-border)] bg-[var(--color-surface-alt)] md:block"
    >
      <div className="flex h-14 items-center pl-3">
      <button
        type="button"
        onClick={toggleCollapsed}
        aria-label={t('chat.expandSidebar')}
        aria-expanded={false}
        aria-controls="workspace-sidebar"
        data-testid="sidebar-expand"
        title={t('chat.expandSidebarShortcut')}
        className="inline-flex shrink-0 h-[var(--control-icon-size)] w-[var(--control-icon-size)] items-center justify-center rounded-[var(--radius-sm)] text-[var(--color-foreground-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-brand-focus)]"
      >
        <PanelLeftOpen className="h-4 w-4" />
      </button>
      </div>
    </div>
  )
}

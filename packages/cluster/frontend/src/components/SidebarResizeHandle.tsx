import type { RefObject } from 'react'
import PanelResizeHandle from '@/components/PanelResizeHandle'
import { SIDEBAR_WIDTH, useSidebarLayout } from '@/hooks/useSidebarLayout'
import { useLocale } from '@/i18n/LocaleProvider'

/** Right-edge resize handle for the desktop sidebar (#967). */
export default function SidebarResizeHandle({ panelRef }: { panelRef: RefObject<HTMLElement | null> }) {
  const { t } = useLocale()
  const { width, setWidth, resetWidth } = useSidebarLayout()
  return (
    <PanelResizeHandle
      panelRef={panelRef}
      controls="workspace-sidebar"
      cssVar="--sidebar-w"
      edge="right"
      bounds={SIDEBAR_WIDTH}
      value={width}
      onCommit={setWidth}
      onReset={resetWidth}
      label={t('chat.resizeSidebar')}
      testId="sidebar-resize-handle"
      showClassName="md:block"
    />
  )
}

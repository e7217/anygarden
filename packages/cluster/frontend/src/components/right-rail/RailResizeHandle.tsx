import type { RefObject } from 'react'
import PanelResizeHandle from '@/components/PanelResizeHandle'
import { RIGHT_RAIL_WIDTH, useRightSidebarLayout } from '@/hooks/useRightSidebarLayout'
import { useLocale } from '@/i18n/LocaleProvider'

interface RailResizeHandleProps {
  /** The panel whose ``--right-rail-w`` the drag previews. */
  panelRef: RefObject<HTMLElement | null>
  /** ``id`` of that panel, for ``aria-controls``. */
  controls: string
}

/** Left-edge resize handle for the right-hand slot (#760). */
export default function RailResizeHandle({ panelRef, controls }: RailResizeHandleProps) {
  const { t } = useLocale()
  const { width, setWidth, resetWidth } = useRightSidebarLayout()
  return (
    <PanelResizeHandle
      panelRef={panelRef}
      controls={controls}
      cssVar="--right-rail-w"
      edge="left"
      bounds={RIGHT_RAIL_WIDTH}
      value={width}
      onCommit={setWidth}
      onReset={resetWidth}
      label={t('chat.resizeContext')}
      testId="right-rail-resize-handle"
      showClassName="lg:block"
    />
  )
}

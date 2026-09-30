import { useRef, type KeyboardEvent, type PointerEvent, type RefObject } from 'react'
import {
  RIGHT_RAIL_WIDTH,
  clampRailWidth,
  useRightSidebarLayout,
} from '@/hooks/useRightSidebarLayout'
import { useLocale } from '@/i18n/LocaleProvider'

interface RailResizeHandleProps {
  /** The panel whose ``--right-rail-w`` the drag previews. */
  panelRef: RefObject<HTMLElement | null>
  /** ``id`` of that panel, for ``aria-controls``. */
  controls: string
}

/**
 * Left-edge resize handle for the right-hand slot (#760). Desktop only;
 * the mobile drawer keeps its fixed width.
 *
 * A drag writes the CSS variable on the panel directly and commits to
 * the layout context once on release, so the chat column does not
 * re-render and localStorage is not written on every pointer move.
 */
export default function RailResizeHandle({ panelRef, controls }: RailResizeHandleProps) {
  const { t } = useLocale()
  const { width, setWidth, resetWidth } = useRightSidebarLayout()
  const drag = useRef<{ startX: number; startWidth: number; current: number } | null>(null)

  const endDrag = (commit: boolean) => {
    const state = drag.current
    if (!state) return
    drag.current = null
    const panel = panelRef.current
    if (panel) delete panel.dataset.resizing
    document.body.style.removeProperty('cursor')
    document.body.style.removeProperty('user-select')
    if (commit) setWidth(state.current)
    else panel?.style.setProperty('--right-rail-w', `${state.startWidth}px`)
  }

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return
    e.preventDefault()
    e.currentTarget.setPointerCapture(e.pointerId)
    drag.current = { startX: e.clientX, startWidth: width, current: width }
    const panel = panelRef.current
    if (panel) panel.dataset.resizing = 'true'
    // Keep the cursor and suppress text selection while the pointer is
    // over the chat column mid-drag.
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }

  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    const state = drag.current
    if (!state) return
    // The rail sits on the right, so moving left widens it.
    state.current = clampRailWidth(state.startWidth + state.startX - e.clientX)
    panelRef.current?.style.setProperty('--right-rail-w', `${state.current}px`)
  }

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const next =
      e.key === 'ArrowLeft' ? width + RIGHT_RAIL_WIDTH.step
      : e.key === 'ArrowRight' ? width - RIGHT_RAIL_WIDTH.step
      : e.key === 'Home' ? RIGHT_RAIL_WIDTH.min
      : e.key === 'End' ? RIGHT_RAIL_WIDTH.max
      : null
    if (next === null) return
    e.preventDefault()
    setWidth(next)
  }

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-controls={controls}
      aria-label={t('chat.resizeContext')}
      aria-valuemin={RIGHT_RAIL_WIDTH.min}
      aria-valuemax={RIGHT_RAIL_WIDTH.max}
      aria-valuenow={width}
      tabIndex={0}
      title={t('chat.resizeContext')}
      data-testid="right-rail-resize-handle"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={() => endDrag(true)}
      onPointerCancel={() => endDrag(false)}
      onLostPointerCapture={() => endDrag(true)}
      onKeyDown={onKeyDown}
      onDoubleClick={resetWidth}
      className="group absolute inset-y-0 -left-1 z-10 hidden w-2 cursor-col-resize touch-none focus-visible:outline-none lg:block"
    >
      {/* Sits over the panel's 1px border; only the accent shows on hover,
          focus and drag, so the resting edge stays whisper-weight. */}
      <span
        aria-hidden="true"
        className="pointer-events-none absolute inset-y-0 left-1/2 w-0.5 -translate-x-1/2 bg-transparent transition-colors group-hover:bg-[var(--color-brand)] group-focus-visible:bg-[var(--color-brand)] group-active:bg-[var(--color-brand)]"
      />
    </div>
  )
}

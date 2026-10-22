import { useRef, type KeyboardEvent, type PointerEvent, type RefObject } from 'react'
import { SIDEBAR_SPLIT, clampSplitRatio } from '@/hooks/useSidebarSplit'
import { useLocale } from '@/i18n/LocaleProvider'

/** CSS variable on the split container holding the agents section's
 *  share of the height, as a percentage. */
export const SIDEBAR_SPLIT_VAR = '--sidebar-split'

export function splitPercent(ratio: number): string {
  return `${Math.round(ratio * 1000) / 10}%`
}

interface SidebarSplitHandleProps {
  /** Element that holds both sections and owns ``--sidebar-split``. */
  containerRef: RefObject<HTMLElement | null>
  /** ``id`` of the agents section, for ``aria-controls``. */
  controls: string
  ratio: number
  onRatioChange: (ratio: number) => void
  onReset: () => void
}

/**
 * Boundary between the sidebar's projects and agents sections (#928).
 * Mirrors the right rail's RailResizeHandle (#760) on the other axis:
 * a drag writes the CSS variable on the container directly and commits
 * once on release, so the sidebar tree does not re-render per pointer
 * move and localStorage is written once per gesture.
 */
export default function SidebarSplitHandle({
  containerRef,
  controls,
  ratio,
  onRatioChange,
  onReset,
}: SidebarSplitHandleProps) {
  const { t } = useLocale()
  const drag = useRef<{ bottom: number; height: number; start: number; current: number } | null>(null)

  const endDrag = (commit: boolean) => {
    const state = drag.current
    if (!state) return
    drag.current = null
    const container = containerRef.current
    if (container) delete container.dataset.resizing
    document.body.style.removeProperty('cursor')
    document.body.style.removeProperty('user-select')
    if (commit) onRatioChange(state.current)
    else container?.style.setProperty(SIDEBAR_SPLIT_VAR, splitPercent(state.start))
  }

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return
    const container = containerRef.current
    if (!container) return
    const rect = container.getBoundingClientRect()
    if (rect.height <= 0) return
    e.preventDefault()
    e.currentTarget.setPointerCapture(e.pointerId)
    drag.current = { bottom: rect.bottom, height: rect.height, start: ratio, current: ratio }
    container.dataset.resizing = 'true'
    // Keep the cursor and suppress text selection while the pointer
    // wanders over the room list mid-drag.
    document.body.style.cursor = 'row-resize'
    document.body.style.userSelect = 'none'
  }

  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    const state = drag.current
    if (!state) return
    // The agents section sits below the handle, so moving up grows it.
    state.current = clampSplitRatio((state.bottom - e.clientY) / state.height)
    containerRef.current?.style.setProperty(SIDEBAR_SPLIT_VAR, splitPercent(state.current))
  }

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const next =
      e.key === 'ArrowUp' ? ratio + SIDEBAR_SPLIT.step
      : e.key === 'ArrowDown' ? ratio - SIDEBAR_SPLIT.step
      : e.key === 'Home' ? SIDEBAR_SPLIT.min
      : e.key === 'End' ? SIDEBAR_SPLIT.max
      : null
    if (next === null) return
    e.preventDefault()
    onRatioChange(next)
  }

  return (
    <div
      role="separator"
      aria-orientation="horizontal"
      aria-controls={controls}
      aria-label={t('chat.resizeSidebarSections')}
      aria-valuemin={Math.round(SIDEBAR_SPLIT.min * 100)}
      aria-valuemax={Math.round(SIDEBAR_SPLIT.max * 100)}
      aria-valuenow={Math.round(ratio * 100)}
      tabIndex={0}
      title={t('chat.resizeSidebarSections')}
      data-testid="sidebar-split-handle"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={() => endDrag(true)}
      onPointerCancel={() => endDrag(false)}
      onLostPointerCapture={() => endDrag(true)}
      onKeyDown={onKeyDown}
      onDoubleClick={onReset}
      className="group relative z-10 -my-1 h-2 shrink-0 cursor-row-resize touch-none focus-visible:outline-none pointer-coarse:-my-2 pointer-coarse:h-4"
    >
      {/* Sits over the agents section's 1px top border; only the accent
          shows on hover, focus and drag, so the resting edge stays
          whisper-weight. */}
      <span
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 top-1/2 h-0.5 -translate-y-1/2 bg-transparent transition-colors group-hover:bg-[var(--color-brand)] group-focus-visible:bg-[var(--color-brand)] group-active:bg-[var(--color-brand)]"
      />
    </div>
  )
}

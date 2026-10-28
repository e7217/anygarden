import { useRef, type KeyboardEvent, type PointerEvent, type RefObject } from 'react'

export interface PanelWidthBounds {
  default: number
  min: number
  max: number
  step: number
}

interface PanelResizeHandleProps {
  /** The panel whose ``cssVar`` the drag previews. */
  panelRef: RefObject<HTMLElement | null>
  /** ``id`` of that panel, for ``aria-controls``. */
  controls: string
  /** Custom property holding the panel's desktop width, e.g. ``--sidebar-w``. */
  cssVar: string
  /** The panel edge the handle sits on. A handle on the left edge (the
   *  right rail) widens its panel when dragged left; one on the right
   *  edge (the sidebar) widens it when dragged right. */
  edge: 'left' | 'right'
  bounds: PanelWidthBounds
  /** Persisted width in px. */
  value: number
  /** Clamp + persist. Called once per gesture or key press. */
  onCommit: (px: number) => void
  onReset: () => void
  label: string
  testId: string
  /** Breakpoint class that shows the handle, e.g. ``lg:block``: the
   *  handle exists only where the panel is a desktop column. */
  showClassName: string
}

/**
 * Horizontal resize handle shared by the sidebar and the right-hand
 * slot (#760, #967). Desktop only; mobile drawers keep a fixed width.
 *
 * A drag writes the CSS variable on the panel directly and commits once
 * on release, so the chat column does not re-render and localStorage is
 * not written on every pointer move.
 *
 * The chat column has a minimum width (#967) and the panels shrink to
 * make room for it, so the width on screen can be narrower than the
 * stored one. Gestures start from, and commit, the width actually on
 * screen; otherwise narrowing a squeezed panel would do nothing until
 * the pointer had travelled past the hidden surplus.
 */
export default function PanelResizeHandle({
  panelRef, controls, cssVar, edge, bounds, value, onCommit, onReset, label, testId, showClassName,
}: PanelResizeHandleProps) {
  const drag = useRef<{ startX: number; startWidth: number; current: number } | null>(null)
  const clamp = (px: number) => Math.round(Math.min(bounds.max, Math.max(bounds.min, px)))

  // jsdom and a panel that is not laid out report 0; fall back to the
  // stored width then.
  const shownWidth = () => {
    const rendered = panelRef.current?.getBoundingClientRect().width ?? 0
    return rendered > 0 ? Math.min(value, Math.round(rendered)) : value
  }

  const endDrag = (commit: boolean) => {
    const state = drag.current
    if (!state) return
    drag.current = null
    const panel = panelRef.current
    if (panel) delete panel.dataset.resizing
    document.body.style.removeProperty('cursor')
    document.body.style.removeProperty('user-select')
    if (!commit) {
      panel?.style.setProperty(cssVar, `${value}px`)
      return
    }
    const rendered = panel?.getBoundingClientRect().width ?? 0
    onCommit(rendered > 0 ? Math.min(state.current, Math.round(rendered)) : state.current)
  }

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return
    e.preventDefault()
    e.currentTarget.setPointerCapture(e.pointerId)
    const startWidth = shownWidth()
    drag.current = { startX: e.clientX, startWidth, current: startWidth }
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
    const delta = e.clientX - state.startX
    state.current = clamp(state.startWidth + (edge === 'left' ? -delta : delta))
    panelRef.current?.style.setProperty(cssVar, `${state.current}px`)
  }

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const widen = edge === 'left' ? 'ArrowLeft' : 'ArrowRight'
    const narrow = edge === 'left' ? 'ArrowRight' : 'ArrowLeft'
    const next =
      e.key === widen ? shownWidth() + bounds.step
      : e.key === narrow ? shownWidth() - bounds.step
      : e.key === 'Home' ? bounds.min
      : e.key === 'End' ? bounds.max
      : null
    if (next === null) return
    e.preventDefault()
    onCommit(next)
  }

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-controls={controls}
      aria-label={label}
      aria-valuemin={bounds.min}
      aria-valuemax={bounds.max}
      aria-valuenow={value}
      tabIndex={0}
      title={label}
      data-testid={testId}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={() => endDrag(true)}
      onPointerCancel={() => endDrag(false)}
      onLostPointerCapture={() => endDrag(true)}
      onKeyDown={onKeyDown}
      onDoubleClick={onReset}
      // The rail's handle straddles its border because the rail follows
      // the chat column in the DOM and paints over it. The sidebar comes
      // first and ``<main>`` is a container (its own stacking context),
      // so an overhang there would sit under the chat column; that
      // handle stays inside the sidebar, ending on its border.
      className={`group absolute inset-y-0 z-10 hidden w-2 cursor-col-resize touch-none focus-visible:outline-none ${showClassName} ${edge === 'left' ? '-left-1' : '-right-px'}`}
    >
      {/* Sits over the panel's 1px border; only the accent shows on hover,
          focus and drag, so the resting edge stays whisper-weight. */}
      <span
        aria-hidden="true"
        className={`pointer-events-none absolute inset-y-0 w-0.5 ${edge === 'left' ? 'left-1/2 -translate-x-1/2' : 'right-0'} bg-transparent transition-colors group-hover:bg-[var(--color-brand)] group-focus-visible:bg-[var(--color-brand)] group-active:bg-[var(--color-brand)]`}
      />
    </div>
  )
}

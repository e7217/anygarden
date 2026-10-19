import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { Minimize2, ZoomIn, ZoomOut } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useLocale } from '@/i18n/LocaleProvider'
import {
  FIT,
  MAX_SCALE,
  MIN_SCALE,
  actualScale,
  panBy,
  zoomAt,
  type Point,
  type ZoomState,
} from '@/lib/imageZoom'

/** One toolbar or key step. */
const STEP = 1.25
/** Wheel delta → zoom factor. Trackpad pinches arrive as ``ctrlKey`` wheel
 * events with small deltas, so they get a steeper curve. */
const WHEEL_SPEED = 0.0015
const PINCH_SPEED = 0.01
const LINE_HEIGHT_PX = 16

/** #882 — an attachment image you can zoom (wheel, pinch, double click,
 * toolbar, ``+ - 0``) and, once zoomed, drag around. ``scale = 1`` is the
 * image fitted to the viewport; see ``lib/imageZoom`` for the geometry. */
export default function ImageZoomViewer({ src, alt }: { src: string; alt: string }) {
  const { t } = useLocale()
  const hintId = useId()
  const viewportRef = useRef<HTMLDivElement>(null)
  const imageRef = useRef<HTMLImageElement>(null)
  const [state, setState] = useState<ZoomState>(FIT)
  // Toolbar and key steps glide; wheel, pinch and drag follow the input.
  const [smooth, setSmooth] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [actual, setActual] = useState(MIN_SCALE)
  const pointers = useRef(new Map<number, Point>())
  const pinchDistance = useRef<number | null>(null)

  const geometry = useCallback(() => {
    const viewport = viewportRef.current
    const image = imageRef.current
    return {
      // Layout sizes ignore the CSS transform: this is the fitted image.
      fitted: { width: image?.offsetWidth ?? 0, height: image?.offsetHeight ?? 0 },
      viewport: { width: viewport?.clientWidth ?? 0, height: viewport?.clientHeight ?? 0 },
    }
  }, [])

  /** Client coordinates → viewport-centre coordinates. */
  const toLocal = useCallback((clientX: number, clientY: number): Point => {
    const rect = viewportRef.current?.getBoundingClientRect()
    if (!rect) return { x: 0, y: 0 }
    return { x: clientX - rect.left - rect.width / 2, y: clientY - rect.top - rect.height / 2 }
  }, [])

  const zoom = useCallback(
    (next: (scale: number) => number, point: Point = { x: 0, y: 0 }, glide = false) => {
      const { fitted, viewport } = geometry()
      setSmooth(glide)
      setState(prev => zoomAt(prev, next(prev.scale), point, fitted, viewport))
    },
    [geometry],
  )

  const measure = useCallback(() => {
    const image = imageRef.current
    if (!image) return
    const { fitted } = geometry()
    setActual(actualScale({ width: image.naturalWidth, height: image.naturalHeight }, fitted))
  }, [geometry])

  // React registers wheel listeners as passive, which can't stop the
  // dialog from scrolling; attach one that can.
  useEffect(() => {
    const viewport = viewportRef.current
    if (!viewport) return
    const onWheel = (event: WheelEvent) => {
      event.preventDefault()
      const delta = event.deltaMode === 1 ? event.deltaY * LINE_HEIGHT_PX : event.deltaY
      const factor = Math.exp(-delta * (event.ctrlKey ? PINCH_SPEED : WHEEL_SPEED))
      zoom(scale => scale * factor, toLocal(event.clientX, event.clientY))
    }
    viewport.addEventListener('wheel', onWheel, { passive: false })
    return () => viewport.removeEventListener('wheel', onWheel)
  }, [zoom, toLocal])

  // A resized viewport changes the fitted size, and with it what 100% is.
  useEffect(() => {
    const viewport = viewportRef.current
    if (!viewport || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => {
      measure()
      setState(FIT)
    })
    observer.observe(viewport)
    return () => observer.disconnect()
  }, [measure])

  const onPointerDown = (event: React.PointerEvent) => {
    if (event.pointerType === 'mouse' && event.button !== 0) return
    event.currentTarget.setPointerCapture?.(event.pointerId)
    pointers.current.set(event.pointerId, toLocal(event.clientX, event.clientY))
    if (pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()]
      pinchDistance.current = Math.hypot(a.x - b.x, a.y - b.y)
    }
    setDragging(pointers.current.size === 1 && state.scale > MIN_SCALE)
  }

  const onPointerMove = (event: React.PointerEvent) => {
    const previous = pointers.current.get(event.pointerId)
    if (!previous) return
    const current = toLocal(event.clientX, event.clientY)
    pointers.current.set(event.pointerId, current)
    const { fitted, viewport } = geometry()
    setSmooth(false)
    if (pointers.current.size === 2 && pinchDistance.current) {
      const [a, b] = [...pointers.current.values()]
      const distance = Math.hypot(a.x - b.x, a.y - b.y)
      const factor = distance / pinchDistance.current
      pinchDistance.current = distance
      const middle = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }
      setState(prev => zoomAt(prev, prev.scale * factor, middle, fitted, viewport))
    } else if (pointers.current.size === 1) {
      const delta = { x: current.x - previous.x, y: current.y - previous.y }
      setState(prev => panBy(prev, delta, fitted, viewport))
    }
  }

  const onPointerEnd = (event: React.PointerEvent) => {
    pointers.current.delete(event.pointerId)
    if (pointers.current.size < 2) pinchDistance.current = null
    setDragging(false)
  }

  const onDoubleClick = (event: React.MouseEvent) => {
    if (state.scale > MIN_SCALE) {
      setSmooth(true)
      setState(FIT)
      return
    }
    // A small image is already at 100% when fitted; give it a closer look.
    const target = actual > MIN_SCALE ? actual : 2
    zoom(() => target, toLocal(event.clientX, event.clientY), true)
  }

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === '+' || event.key === '=') zoom(scale => scale * STEP, undefined, true)
    else if (event.key === '-') zoom(scale => scale / STEP, undefined, true)
    else if (event.key === '0') zoom(() => MIN_SCALE, undefined, true)
    else return
    event.preventDefault()
  }

  const fitted = state.scale === MIN_SCALE
  const level = `${Math.round((state.scale / actual) * 100)}%`
  const cursor = fitted ? 'cursor-zoom-in' : dragging ? 'cursor-grabbing' : 'cursor-grab'

  return (
    <div className="flex flex-col gap-2">
      <div
        ref={viewportRef}
        data-testid="image-zoom-viewport"
        role="group"
        tabIndex={0}
        aria-label={t('chat.imageViewerLabel', { name: alt })}
        aria-describedby={hintId}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerEnd}
        onPointerCancel={onPointerEnd}
        onDoubleClick={onDoubleClick}
        onKeyDown={onKeyDown}
        className={`relative flex h-[min(72vh,48rem)] touch-none select-none items-center justify-center overflow-hidden rounded-[var(--radius-sm)] bg-[var(--color-surface-alt)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-brand-focus)] ${cursor}`}
      >
        <img
          ref={imageRef}
          src={src}
          alt={alt}
          draggable={false}
          onLoad={measure}
          style={{
            transform: `translate(${state.offset.x}px, ${state.offset.y}px) scale(${state.scale})`,
          }}
          className={`max-h-full max-w-full object-contain will-change-transform ${
            smooth ? 'motion-safe:transition-transform motion-safe:duration-150' : ''
          }`}
        />
        <p id={hintId} className="sr-only">{t('chat.imageViewerHint')}</p>
      </div>
      <div
        role="toolbar"
        aria-label={t('chat.imageZoomToolbar')}
        className="flex items-center justify-center gap-1"
      >
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label={t('chat.imageZoomOut')}
          title={t('chat.imageZoomOut')}
          disabled={fitted}
          onClick={() => zoom(scale => scale / STEP, undefined, true)}
        >
          <ZoomOut className="h-4 w-4" aria-hidden="true" />
        </Button>
        <Button
          type="button"
          variant="ghost"
          aria-label={t('chat.imageZoomActual', { level })}
          title={t('chat.imageZoomActual', { level })}
          onClick={() => zoom(() => actual, undefined, true)}
          className="min-w-16 tabular-nums"
        >
          {level}
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label={t('chat.imageZoomIn')}
          title={t('chat.imageZoomIn')}
          disabled={state.scale >= MAX_SCALE}
          onClick={() => zoom(scale => scale * STEP, undefined, true)}
        >
          <ZoomIn className="h-4 w-4" aria-hidden="true" />
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label={t('chat.imageZoomFit')}
          title={t('chat.imageZoomFit')}
          disabled={fitted}
          onClick={() => zoom(() => MIN_SCALE, undefined, true)}
        >
          <Minimize2 className="h-4 w-4" aria-hidden="true" />
        </Button>
      </div>
    </div>
  )
}

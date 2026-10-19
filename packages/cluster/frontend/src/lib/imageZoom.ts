/**
 * Geometry for the attachment image viewer (#882).
 *
 * The image is laid out at its fitted size (``object-contain`` in the
 * viewport) and the viewer adds ``translate(offset) scale(scale)`` on top,
 * so ``scale = 1`` is "fit to screen". Points and offsets are in pixels
 * relative to the viewport centre, which is the transform origin.
 */

export interface Point {
  x: number
  y: number
}

export interface Size {
  width: number
  height: number
}

export interface ZoomState {
  scale: number
  offset: Point
}

export const MIN_SCALE = 1
export const MAX_SCALE = 8
export const FIT: ZoomState = { scale: MIN_SCALE, offset: { x: 0, y: 0 } }

export function clampScale(scale: number): number {
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale))
}

const clampAxis = (value: number, fitted: number, scale: number, viewport: number) => {
  const room = Math.max(0, (fitted * scale - viewport) / 2)
  // ``+ 0`` folds -0 into 0 so a centred axis compares equal to 0.
  return Math.min(room, Math.max(-room, value)) + 0
}

/** Keep the zoomed image's edges from coming inside the viewport. */
export function clampOffset(offset: Point, scale: number, fitted: Size, viewport: Size): Point {
  return {
    x: clampAxis(offset.x, fitted.width, scale, viewport.width),
    y: clampAxis(offset.y, fitted.height, scale, viewport.height),
  }
}

/** Zoom to *nextScale* while the image pixel under *point* stays put. */
export function zoomAt(
  state: ZoomState,
  nextScale: number,
  point: Point,
  fitted: Size,
  viewport: Size,
): ZoomState {
  const scale = clampScale(nextScale)
  if (scale === MIN_SCALE) return FIT
  const ratio = scale / state.scale
  const offset = {
    x: point.x - (point.x - state.offset.x) * ratio,
    y: point.y - (point.y - state.offset.y) * ratio,
  }
  return { scale, offset: clampOffset(offset, scale, fitted, viewport) }
}

export function panBy(state: ZoomState, delta: Point, fitted: Size, viewport: Size): ZoomState {
  if (state.scale === MIN_SCALE) return state
  const offset = { x: state.offset.x + delta.x, y: state.offset.y + delta.y }
  return { scale: state.scale, offset: clampOffset(offset, state.scale, fitted, viewport) }
}

/** The scale at which one image pixel is one screen pixel (100%). An image
 * already shown at its natural size or smaller is at 100% when fitted. */
export function actualScale(natural: Size, fitted: Size): number {
  if (fitted.width <= 0 || natural.width <= 0) return MIN_SCALE
  return Math.max(MIN_SCALE, natural.width / fitted.width)
}

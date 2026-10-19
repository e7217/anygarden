import { describe, expect, it } from 'vitest'
import {
  FIT,
  MAX_SCALE,
  actualScale,
  clampOffset,
  clampScale,
  panBy,
  zoomAt,
} from './imageZoom'

// A 400×300 image fitted inside an 800×600 viewport.
const fitted = { width: 400, height: 300 }
const viewport = { width: 800, height: 600 }

describe('clampScale', () => {
  it('keeps the scale between fit and the maximum', () => {
    expect(clampScale(0.3)).toBe(1)
    expect(clampScale(3)).toBe(3)
    expect(clampScale(50)).toBe(MAX_SCALE)
  })
})

describe('clampOffset', () => {
  it('pins a fitted image to the centre', () => {
    expect(clampOffset({ x: 120, y: -40 }, 1, fitted, viewport)).toEqual({ x: 0, y: 0 })
  })

  it('lets a zoomed image move only until its edge reaches the viewport edge', () => {
    // 4× → 1600×1200; it may slide (1600-800)/2 = 400 by (1200-600)/2 = 300.
    expect(clampOffset({ x: 999, y: -999 }, 4, fitted, viewport)).toEqual({ x: 400, y: -300 })
    expect(clampOffset({ x: 100, y: 50 }, 4, fitted, viewport)).toEqual({ x: 100, y: 50 })
  })

  it('keeps an axis centred while the zoomed image still fits on it', () => {
    // 2× → 800×600: exactly the viewport, no room to move either way.
    expect(clampOffset({ x: 30, y: 30 }, 2, fitted, viewport)).toEqual({ x: 0, y: 0 })
  })
})

describe('zoomAt', () => {
  it('keeps the point under the cursor in place', () => {
    const point = { x: 100, y: 50 }
    const next = zoomAt(FIT, 4, point, fitted, viewport)
    expect(next.scale).toBe(4)
    // Image-space position of the point before and after must match.
    const before = { x: (point.x - FIT.offset.x) / FIT.scale, y: (point.y - FIT.offset.y) / FIT.scale }
    const after = { x: (point.x - next.offset.x) / next.scale, y: (point.y - next.offset.y) / next.scale }
    expect(after).toEqual(before)
  })

  it('clamps the scale and recentres when zooming back to fit', () => {
    const zoomed = zoomAt(FIT, 4, { x: 200, y: 100 }, fitted, viewport)
    expect(zoomAt(zoomed, 20, { x: 0, y: 0 }, fitted, viewport).scale).toBe(MAX_SCALE)
    expect(zoomAt(zoomed, 0.1, { x: 200, y: 100 }, fitted, viewport)).toEqual(FIT)
  })

  it('does not let the image drift out of view', () => {
    const next = zoomAt(FIT, 4, { x: 400, y: 300 }, fitted, viewport)
    expect(Math.abs(next.offset.x)).toBeLessThanOrEqual(400)
    expect(Math.abs(next.offset.y)).toBeLessThanOrEqual(300)
  })
})

describe('panBy', () => {
  it('moves a zoomed image within its bounds', () => {
    const zoomed = { scale: 4, offset: { x: 0, y: 0 } }
    expect(panBy(zoomed, { x: 50, y: -20 }, fitted, viewport).offset).toEqual({ x: 50, y: -20 })
    expect(panBy(zoomed, { x: 5000, y: 0 }, fitted, viewport).offset).toEqual({ x: 400, y: 0 })
  })

  it('does nothing at fit', () => {
    expect(panBy(FIT, { x: 50, y: 50 }, fitted, viewport)).toEqual(FIT)
  })
})

describe('actualScale', () => {
  it('is the natural width over the fitted width', () => {
    expect(actualScale({ width: 2000, height: 1500 }, fitted)).toBe(5)
  })

  it('is never below fit', () => {
    expect(actualScale({ width: 200, height: 150 }, fitted)).toBe(1)
    expect(actualScale({ width: 0, height: 0 }, { width: 0, height: 0 })).toBe(1)
  })
})

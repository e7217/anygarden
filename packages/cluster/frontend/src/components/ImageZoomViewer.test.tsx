// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import ImageZoomViewer from './ImageZoomViewer'

// jsdom has no layout: every size is 0, so 100% equals fit and the toolbar
// steps are what we can observe. Real geometry is covered in
// e2e/image-viewer.spec.ts.

afterEach(cleanup)

const scaleOf = (img: HTMLElement) => {
  const match = /scale\(([\d.]+)\)/.exec(img.style.transform)
  return match ? Number(match[1]) : NaN
}

function renderViewer() {
  render(<ImageZoomViewer src="blob:shot" alt="shot.png" />)
  return {
    image: screen.getByRole('img', { name: 'shot.png' }),
    viewport: screen.getByTestId('image-zoom-viewport'),
    level: screen.getByRole('button', { name: /Actual size/ }),
  }
}

describe('ImageZoomViewer', () => {
  it('starts fitted with zoom-out and fit unavailable', () => {
    const { image, level } = renderViewer()
    expect(scaleOf(image)).toBe(1)
    expect(level).toHaveTextContent('100%')
    expect(screen.getByRole('button', { name: 'Zoom out' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Fit to screen' })).toBeDisabled()
  })

  it('zooms in and back out with the toolbar', () => {
    const { image, level } = renderViewer()
    fireEvent.click(screen.getByRole('button', { name: 'Zoom in' }))
    expect(scaleOf(image)).toBeCloseTo(1.25)
    expect(level).toHaveTextContent('125%')
    expect(screen.getByRole('button', { name: 'Fit to screen' })).toBeEnabled()

    fireEvent.click(screen.getByRole('button', { name: 'Zoom out' }))
    expect(scaleOf(image)).toBe(1)
  })

  it('stops at the maximum zoom', () => {
    const { image } = renderViewer()
    const zoomIn = screen.getByRole('button', { name: 'Zoom in' })
    for (let i = 0; i < 20; i += 1) fireEvent.click(zoomIn)
    expect(scaleOf(image)).toBe(8)
    expect(zoomIn).toBeDisabled()
  })

  it('takes + - and 0 from the keyboard', () => {
    const { image, viewport } = renderViewer()
    fireEvent.keyDown(viewport, { key: '+' })
    fireEvent.keyDown(viewport, { key: '=' })
    expect(scaleOf(image)).toBeCloseTo(1.5625)
    fireEvent.keyDown(viewport, { key: '-' })
    expect(scaleOf(image)).toBeCloseTo(1.25)
    fireEvent.keyDown(viewport, { key: '0' })
    expect(scaleOf(image)).toBe(1)
  })

  it('toggles between fit and a closer look on double click', () => {
    const { image, viewport } = renderViewer()
    fireEvent.doubleClick(viewport, { clientX: 0, clientY: 0 })
    expect(scaleOf(image)).toBe(2)
    fireEvent.doubleClick(viewport, { clientX: 0, clientY: 0 })
    expect(scaleOf(image)).toBe(1)
  })

  it('is a focusable, labelled region', () => {
    const { viewport } = renderViewer()
    expect(viewport).toHaveAttribute('tabindex', '0')
    expect(viewport).toHaveAccessibleName(/shot\.png/)
    expect(screen.getByRole('toolbar', { name: 'Zoom' })).toBeInTheDocument()
  })
})

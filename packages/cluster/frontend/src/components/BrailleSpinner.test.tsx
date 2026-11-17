// @vitest-environment jsdom
import { act, cleanup, render } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import BrailleSpinner from './BrailleSpinner'

function setVisibility(state: DocumentVisibilityState) {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state })
  document.dispatchEvent(new Event('visibilitychange'))
}

function mockReducedMotion(reduce: boolean) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: reduce && query.includes('reduce'),
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  })) as unknown as typeof window.matchMedia
}

beforeEach(() => {
  vi.useFakeTimers()
  setVisibility('visible')
  mockReducedMotion(false)
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('BrailleSpinner', () => {
  it('is hidden from assistive technology and turns', () => {
    const { container } = render(<BrailleSpinner />)
    const glyph = container.querySelector('span')!
    expect(glyph).toHaveAttribute('aria-hidden', 'true')
    const before = glyph.textContent
    act(() => { vi.advanceTimersByTime(80) })
    expect(glyph.textContent).not.toBe(before)
  })

  it('pauses while the tab is hidden and resumes when shown', () => {
    const { container } = render(<BrailleSpinner />)
    const glyph = container.querySelector('span')!
    act(() => { setVisibility('hidden') })
    const paused = glyph.textContent
    act(() => { vi.advanceTimersByTime(800) })
    expect(glyph.textContent).toBe(paused)
    act(() => { setVisibility('visible') })
    act(() => { vi.advanceTimersByTime(80) })
    expect(glyph.textContent).not.toBe(paused)
  })

  it('keeps a slow turn under reduced motion', () => {
    mockReducedMotion(true)
    const { container } = render(<BrailleSpinner />)
    const glyph = container.querySelector('span')!
    const before = glyph.textContent
    act(() => { vi.advanceTimersByTime(80) })
    expect(glyph.textContent).toBe(before)
    act(() => { vi.advanceTimersByTime(400) })
    expect(glyph.textContent).not.toBe(before)
  })
})

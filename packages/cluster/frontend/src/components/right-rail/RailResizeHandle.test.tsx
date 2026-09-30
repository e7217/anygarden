// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { createRef, type ReactNode } from 'react'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import RailResizeHandle from './RailResizeHandle'
import { RightSidebarLayoutProvider, RIGHT_RAIL_WIDTH } from '@/hooks/useRightSidebarLayout'
import { LocaleProvider } from '@/i18n/LocaleProvider'

const WIDTH_KEY = 'anygarden_right_sidebar_width'

function Wrapper({ children }: { children: ReactNode }) {
  return (
    <LocaleProvider>
      <RightSidebarLayoutProvider>{children}</RightSidebarLayoutProvider>
    </LocaleProvider>
  )
}

function setup() {
  const panelRef = createRef<HTMLElement>()
  render(
    <Wrapper>
      <aside ref={panelRef} id="panel">
        <RailResizeHandle panelRef={panelRef} controls="panel" />
      </aside>
    </Wrapper>,
  )
  const handle = screen.getByRole('separator')
  return { handle, panel: panelRef.current! }
}

beforeEach(() => {
  localStorage.clear()
  localStorage.setItem('anygarden_locale', 'en')
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: true,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  })
  // jsdom lacks pointer capture.
  HTMLElement.prototype.setPointerCapture = vi.fn()
  HTMLElement.prototype.releasePointerCapture = vi.fn()
  HTMLElement.prototype.hasPointerCapture = vi.fn(() => true)
})

afterEach(() => {
  cleanup()
  localStorage.clear()
})

describe('RailResizeHandle', () => {
  it('exposes a labelled vertical separator with its range', () => {
    const { handle } = setup()
    expect(handle.getAttribute('aria-orientation')).toBe('vertical')
    expect(handle.getAttribute('aria-controls')).toBe('panel')
    expect(handle.getAttribute('aria-label')).toBe('Resize context panel')
    expect(handle.getAttribute('aria-valuemin')).toBe(String(RIGHT_RAIL_WIDTH.min))
    expect(handle.getAttribute('aria-valuemax')).toBe(String(RIGHT_RAIL_WIDTH.max))
    expect(handle.getAttribute('aria-valuenow')).toBe('320')
    expect(handle.tabIndex).toBe(0)
  })

  it('ArrowLeft widens and ArrowRight narrows by one step', () => {
    const { handle } = setup()
    fireEvent.keyDown(handle, { key: 'ArrowLeft' })
    expect(handle.getAttribute('aria-valuenow')).toBe('336')
    fireEvent.keyDown(handle, { key: 'ArrowRight' })
    fireEvent.keyDown(handle, { key: 'ArrowRight' })
    expect(handle.getAttribute('aria-valuenow')).toBe('304')
    expect(localStorage.getItem(WIDTH_KEY)).toBe('304')
  })

  it('Home and End jump to the bounds', () => {
    const { handle } = setup()
    fireEvent.keyDown(handle, { key: 'End' })
    expect(handle.getAttribute('aria-valuenow')).toBe(String(RIGHT_RAIL_WIDTH.max))
    fireEvent.keyDown(handle, { key: 'Home' })
    expect(handle.getAttribute('aria-valuenow')).toBe(String(RIGHT_RAIL_WIDTH.min))
  })

  it('double click resets to the default', () => {
    localStorage.setItem(WIDTH_KEY, '500')
    const { handle } = setup()
    fireEvent.doubleClick(handle)
    expect(handle.getAttribute('aria-valuenow')).toBe('320')
  })

  it('drags on the DOM and commits once on release', () => {
    const { handle, panel } = setup()
    const setItem = vi.spyOn(Storage.prototype, 'setItem')

    fireEvent.pointerDown(handle, { button: 0, clientX: 1000, pointerId: 1 })
    expect(panel.dataset.resizing).toBe('true')
    expect(document.body.style.cursor).toBe('col-resize')

    fireEvent.pointerMove(handle, { clientX: 950, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientX: 900, pointerId: 1 })
    expect(panel.style.getPropertyValue('--right-rail-w')).toBe('420px')
    // Nothing is committed mid-gesture.
    expect(handle.getAttribute('aria-valuenow')).toBe('320')
    expect(setItem).not.toHaveBeenCalledWith(WIDTH_KEY, expect.anything())

    act(() => { fireEvent.pointerUp(handle, { clientX: 900, pointerId: 1 }) })
    expect(handle.getAttribute('aria-valuenow')).toBe('420')
    expect(localStorage.getItem(WIDTH_KEY)).toBe('420')
    expect(panel.dataset.resizing).toBeUndefined()
    expect(document.body.style.cursor).toBe('')
    setItem.mockRestore()
  })

  it('clamps a drag past the bounds', () => {
    const { handle, panel } = setup()
    fireEvent.pointerDown(handle, { button: 0, clientX: 1000, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientX: 0, pointerId: 1 })
    expect(panel.style.getPropertyValue('--right-rail-w')).toBe(`${RIGHT_RAIL_WIDTH.max}px`)
    fireEvent.pointerUp(handle, { clientX: 0, pointerId: 1 })
    expect(handle.getAttribute('aria-valuenow')).toBe(String(RIGHT_RAIL_WIDTH.max))
  })

  it('ignores moves without a pressed pointer and non-primary buttons', () => {
    const { handle, panel } = setup()
    fireEvent.pointerMove(handle, { clientX: 500, pointerId: 1 })
    fireEvent.pointerDown(handle, { button: 2, clientX: 1000, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientX: 500, pointerId: 1 })
    expect(panel.style.getPropertyValue('--right-rail-w')).toBe('')
    expect(panel.dataset.resizing).toBeUndefined()
  })
})

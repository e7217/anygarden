// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { createRef, useState } from 'react'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import SidebarSplitHandle, { SIDEBAR_SPLIT_VAR } from './SidebarSplitHandle'
import { SIDEBAR_SPLIT } from '@/hooks/useSidebarSplit'
import { LocaleProvider } from '@/i18n/LocaleProvider'

function Harness({ onCommit }: { onCommit: (r: number) => void }) {
  const [ratio, setRatio] = useState(0.5)
  const containerRef = createRef<HTMLDivElement>()
  return (
    <div ref={containerRef} data-testid="container">
      <SidebarSplitHandle
        containerRef={containerRef}
        controls="agents"
        ratio={ratio}
        onRatioChange={(r) => { onCommit(r); setRatio(r) }}
        onReset={() => setRatio(SIDEBAR_SPLIT.default)}
      />
    </div>
  )
}

function setup() {
  const onCommit = vi.fn()
  render(
    <LocaleProvider>
      <Harness onCommit={onCommit} />
    </LocaleProvider>,
  )
  const container = screen.getByTestId('container')
  // A 400px-tall split container spanning y = 100..500.
  container.getBoundingClientRect = () =>
    ({ top: 100, bottom: 500, height: 400, left: 0, right: 256, width: 256, x: 0, y: 100, toJSON: () => ({}) })
  return { handle: screen.getByRole('separator'), container, onCommit }
}

beforeEach(() => {
  localStorage.clear()
  localStorage.setItem('anygarden_locale', 'en')
  // jsdom lacks pointer capture.
  HTMLElement.prototype.setPointerCapture = vi.fn()
  HTMLElement.prototype.releasePointerCapture = vi.fn()
  HTMLElement.prototype.hasPointerCapture = vi.fn(() => true)
})

afterEach(() => {
  cleanup()
  localStorage.clear()
  document.body.removeAttribute('style')
})

describe('SidebarSplitHandle (#928)', () => {
  it('exposes a labelled horizontal separator with its range', () => {
    const { handle } = setup()
    expect(handle.getAttribute('aria-orientation')).toBe('horizontal')
    expect(handle.getAttribute('aria-controls')).toBe('agents')
    expect(handle.getAttribute('aria-label')).toBe('Resize projects and agents')
    expect(handle.getAttribute('aria-valuemin')).toBe('10')
    expect(handle.getAttribute('aria-valuemax')).toBe('90')
    expect(handle.getAttribute('aria-valuenow')).toBe('50')
    expect(handle.tabIndex).toBe(0)
  })

  it('[ROOM-12] drags on the DOM and commits once on release', () => {
    const { handle, container, onCommit } = setup()

    fireEvent.pointerDown(handle, { button: 0, clientY: 300, pointerId: 1 })
    expect(container.dataset.resizing).toBe('true')
    expect(document.body.style.cursor).toBe('row-resize')

    // Moving up grows the agents section below the handle.
    fireEvent.pointerMove(handle, { clientY: 250, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientY: 220, pointerId: 1 })
    expect(container.style.getPropertyValue(SIDEBAR_SPLIT_VAR)).toBe('70%')
    expect(onCommit).not.toHaveBeenCalled()
    expect(handle.getAttribute('aria-valuenow')).toBe('50')

    act(() => { fireEvent.pointerUp(handle, { clientY: 220, pointerId: 1 }) })
    expect(onCommit).toHaveBeenCalledTimes(1)
    expect(onCommit).toHaveBeenCalledWith(0.7)
    expect(handle.getAttribute('aria-valuenow')).toBe('70')
    expect(container.dataset.resizing).toBeUndefined()
    expect(document.body.style.cursor).toBe('')
  })

  it('clamps a drag past the bounds', () => {
    const { handle, container } = setup()
    fireEvent.pointerDown(handle, { button: 0, clientY: 300, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientY: 0, pointerId: 1 })
    expect(container.style.getPropertyValue(SIDEBAR_SPLIT_VAR)).toBe('90%')
    fireEvent.pointerMove(handle, { clientY: 900, pointerId: 1 })
    expect(container.style.getPropertyValue(SIDEBAR_SPLIT_VAR)).toBe('10%')
  })

  it('restores the starting split when the gesture is cancelled', () => {
    const { handle, container, onCommit } = setup()
    fireEvent.pointerDown(handle, { button: 0, clientY: 300, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientY: 200, pointerId: 1 })
    fireEvent.pointerCancel(handle, { pointerId: 1 })
    expect(container.style.getPropertyValue(SIDEBAR_SPLIT_VAR)).toBe('50%')
    expect(onCommit).not.toHaveBeenCalled()
  })

  it('ignores moves without a pressed pointer and non-primary buttons', () => {
    const { handle, container } = setup()
    fireEvent.pointerMove(handle, { clientY: 200, pointerId: 1 })
    fireEvent.pointerDown(handle, { button: 2, clientY: 300, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientY: 200, pointerId: 1 })
    expect(container.style.getPropertyValue(SIDEBAR_SPLIT_VAR)).toBe('')
    expect(container.dataset.resizing).toBeUndefined()
  })

  it('ArrowUp grows, ArrowDown shrinks, Home/End jump, double click resets', () => {
    const { handle } = setup()
    fireEvent.keyDown(handle, { key: 'ArrowUp' })
    expect(handle.getAttribute('aria-valuenow')).toBe('55')
    fireEvent.keyDown(handle, { key: 'ArrowDown' })
    fireEvent.keyDown(handle, { key: 'ArrowDown' })
    expect(handle.getAttribute('aria-valuenow')).toBe('45')
    fireEvent.keyDown(handle, { key: 'End' })
    expect(handle.getAttribute('aria-valuenow')).toBe('90')
    fireEvent.keyDown(handle, { key: 'Home' })
    expect(handle.getAttribute('aria-valuenow')).toBe('10')
    fireEvent.doubleClick(handle)
    expect(handle.getAttribute('aria-valuenow')).toBe('50')
  })
})

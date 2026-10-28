// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { createRef, useState } from 'react'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import PanelResizeHandle from './PanelResizeHandle'

const BOUNDS = { default: 256, min: 208, max: 480, step: 16 }

function setup(edge: 'left' | 'right', { initial = 256, rendered = 0 } = {}) {
  const panelRef = createRef<HTMLElement>()
  const commits: number[] = []
  function Harness() {
    const [value, setValue] = useState(initial)
    const commit = (px: number) => {
      const next = Math.min(BOUNDS.max, Math.max(BOUNDS.min, px))
      commits.push(next)
      setValue(next)
    }
    return (
      <aside ref={panelRef} id="panel">
        <PanelResizeHandle
          panelRef={panelRef}
          controls="panel"
          cssVar="--panel-w"
          edge={edge}
          bounds={BOUNDS}
          value={value}
          onCommit={commit}
          onReset={() => setValue(BOUNDS.default)}
          label="Resize panel"
          testId="handle"
          showClassName="md:block"
        />
      </aside>
    )
  }
  render(<Harness />)
  const panel = panelRef.current!
  // jsdom has no layout; stand in for the width flex leaves on screen.
  panel.getBoundingClientRect = () => ({ width: rendered }) as DOMRect
  return { handle: screen.getByRole('separator'), panel, commits }
}

beforeEach(() => {
  HTMLElement.prototype.setPointerCapture = vi.fn()
  HTMLElement.prototype.releasePointerCapture = vi.fn()
  HTMLElement.prototype.hasPointerCapture = vi.fn(() => true)
})

afterEach(cleanup)

describe('PanelResizeHandle', () => {
  it('sits on the requested edge and shows only from the given breakpoint', () => {
    const { handle } = setup('right')
    expect(handle.className).toMatch(/-right-px/)
    expect(handle.className).toMatch(/\bhidden\b/)
    expect(handle.className).toMatch(/md:block/)
    expect(handle.getAttribute('aria-label')).toBe('Resize panel')
  })

  it('a right-edge handle widens when dragged right', () => {
    const { handle, panel, commits } = setup('right')
    fireEvent.pointerDown(handle, { button: 0, clientX: 300, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientX: 400, pointerId: 1 })
    expect(panel.style.getPropertyValue('--panel-w')).toBe('356px')
    act(() => { fireEvent.pointerUp(handle, { clientX: 400, pointerId: 1 }) })
    expect(commits).toEqual([356])
    expect(handle.getAttribute('aria-valuenow')).toBe('356')
  })

  it('a right-edge handle maps ArrowRight to wider and ArrowLeft to narrower', () => {
    const { handle } = setup('right')
    fireEvent.keyDown(handle, { key: 'ArrowRight' })
    expect(handle.getAttribute('aria-valuenow')).toBe('272')
    fireEvent.keyDown(handle, { key: 'ArrowLeft' })
    fireEvent.keyDown(handle, { key: 'ArrowLeft' })
    expect(handle.getAttribute('aria-valuenow')).toBe('240')
  })

  it('a left-edge handle widens when dragged left', () => {
    const { handle, panel } = setup('left')
    fireEvent.pointerDown(handle, { button: 0, clientX: 1000, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientX: 900, pointerId: 1 })
    expect(panel.style.getPropertyValue('--panel-w')).toBe('356px')
  })

  it('a cancelled drag restores the stored width without committing', () => {
    const { handle, panel, commits } = setup('right')
    fireEvent.pointerDown(handle, { button: 0, clientX: 300, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientX: 400, pointerId: 1 })
    fireEvent.pointerCancel(handle, { pointerId: 1 })
    expect(panel.style.getPropertyValue('--panel-w')).toBe('256px')
    expect(commits).toEqual([])
    expect(panel.dataset.resizing).toBeUndefined()
  })

  it('[ROOM-13] commits the width on screen when the layout held the panel narrower', () => {
    // Asked for 480, but the chat column's minimum left room for 300.
    const { handle, commits } = setup('right', { rendered: 300 })
    fireEvent.pointerDown(handle, { button: 0, clientX: 300, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientX: 600, pointerId: 1 })
    act(() => { fireEvent.pointerUp(handle, { clientX: 600, pointerId: 1 }) })
    expect(commits).toEqual([300])
  })

  it('[ROOM-13] a squeezed panel narrows from its on-screen width, with no dead zone', () => {
    const { handle, panel } = setup('right', { initial: 480, rendered: 300 })
    fireEvent.pointerDown(handle, { button: 0, clientX: 300, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientX: 280, pointerId: 1 })
    expect(panel.style.getPropertyValue('--panel-w')).toBe('280px')
  })

  it('keyboard steps start from the on-screen width', () => {
    const { handle, commits } = setup('right', { initial: 480, rendered: 300 })
    fireEvent.keyDown(handle, { key: 'ArrowLeft' })
    expect(commits).toEqual([284])
  })
})

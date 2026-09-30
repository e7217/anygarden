// @vitest-environment jsdom
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useElementHeightVar } from './useElementHeightVar'

type Callback = (entries: Array<{ target: Element; borderBoxSize?: Array<{ blockSize: number }> }>) => void

const observers: Array<{ cb: Callback; observe: ReturnType<typeof vi.fn>; disconnect: ReturnType<typeof vi.fn> }> = []

beforeEach(() => {
  observers.length = 0
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe = vi.fn()
      disconnect = vi.fn()
      unobserve = vi.fn()
      constructor(cb: Callback) {
        observers.push({ cb, observe: this.observe, disconnect: this.disconnect })
      }
    },
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function setup() {
  const source = document.createElement('div')
  const target = document.createElement('div')
  const hook = renderHook(() => useElementHeightVar(source, target, '--room-header-h'))
  return { source, target, hook }
}

describe('useElementHeightVar', () => {
  it('observes the source element', () => {
    const { source } = setup()
    expect(observers).toHaveLength(1)
    expect(observers[0].observe).toHaveBeenCalledWith(source)
  })

  it('publishes the border-box height on the target', () => {
    const { source, target } = setup()
    observers[0].cb([{ target: source, borderBoxSize: [{ blockSize: 81 }] }])
    expect(target.style.getPropertyValue('--room-header-h')).toBe('81px')
  })

  it('falls back to the bounding rect without borderBoxSize', () => {
    const { source, target } = setup()
    source.getBoundingClientRect = () => ({ height: 57 }) as DOMRect
    observers[0].cb([{ target: source }])
    expect(target.style.getPropertyValue('--room-header-h')).toBe('57px')
  })

  it('skips writes when the height is unchanged', () => {
    const { source, target } = setup()
    const spy = vi.spyOn(target.style, 'setProperty')
    observers[0].cb([{ target: source, borderBoxSize: [{ blockSize: 57 }] }])
    observers[0].cb([{ target: source, borderBoxSize: [{ blockSize: 57 }] }])
    expect(spy).toHaveBeenCalledTimes(1)
  })

  it('disconnects and clears the variable on unmount', () => {
    const { source, target, hook } = setup()
    observers[0].cb([{ target: source, borderBoxSize: [{ blockSize: 57 }] }])
    hook.unmount()
    expect(observers[0].disconnect).toHaveBeenCalled()
    expect(target.style.getPropertyValue('--room-header-h')).toBe('')
  })

  it('does nothing until both elements exist', () => {
    renderHook(() => useElementHeightVar(null, document.createElement('div'), '--x'))
    expect(observers).toHaveLength(0)
  })

  it('does nothing when ResizeObserver is unavailable', () => {
    vi.stubGlobal('ResizeObserver', undefined)
    expect(() => setup()).not.toThrow()
  })
})

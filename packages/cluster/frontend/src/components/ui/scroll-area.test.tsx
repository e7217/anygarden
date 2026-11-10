// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { ScrollArea, scrollToEnd } from './scroll-area'

afterEach(() => cleanup())

/**
 * #336 — Substrate guard. Radix ``ScrollAreaPrimitive.Viewport`` injects
 * ``display: table; min-width: 100%`` as inline style on its first child
 * wrapper. ``display: table`` sizes that wrapper to descendants' min-content
 * width, which for ``truncate`` (white-space: nowrap) is the full unbroken
 * text width — defeating ``min-w-0`` / ``flex-1`` / ``truncate`` further
 * down the tree. We override that wrapper to ``display: block`` while
 * preserving ``min-width: 100%`` via an arbitrary-variant utility on the
 * Viewport's ``className``. This test pins the override so refactors can't
 * silently drop it.
 */
describe('ScrollArea viewport substrate (#336)', () => {
  it('forces inner wrapper to display:block while keeping min-width:100%', () => {
    const { container } = render(
      <ScrollArea data-testid="scroll-area">
        <div>content</div>
      </ScrollArea>,
    )

    const viewport = container.querySelector(
      '[data-radix-scroll-area-viewport]',
    )
    expect(viewport).not.toBeNull()
    expect(viewport!.className).toContain('[&>div]:!block')
    expect(viewport!.className).toContain('[&>div]:!min-w-full')
  })
})

describe('scrollToEnd (#1013)', () => {
  it('scrolls the viewport itself, never the anchor into view', () => {
    const { container } = render(
      <ScrollArea>
        <div data-testid="bottom" />
      </ScrollArea>,
    )
    const viewport = container.querySelector('[data-radix-scroll-area-viewport]') as HTMLElement
    const bottom = container.querySelector('[data-testid="bottom"]') as HTMLElement
    Object.defineProperty(viewport, 'scrollHeight', { configurable: true, value: 500 })
    const scrollTo = vi.fn()
    viewport.scrollTo = scrollTo as unknown as typeof viewport.scrollTo
    const scrollIntoView = vi.fn()
    bottom.scrollIntoView = scrollIntoView

    scrollToEnd(bottom)

    expect(scrollTo).toHaveBeenCalledWith({ top: 500, behavior: 'smooth' })
    expect(scrollIntoView).not.toHaveBeenCalled()
  })

  it('ignores a missing anchor', () => {
    expect(() => scrollToEnd(null)).not.toThrow()
  })
})

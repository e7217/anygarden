// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { ScrollArea } from './scroll-area'

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

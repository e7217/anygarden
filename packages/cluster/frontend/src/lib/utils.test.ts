import { describe, expect, it } from 'vitest'
import { cn } from './utils'

describe('design system type scale', () => {
  it.each(['display', 'title', 'heading', 'lead', 'caption', 'badge'])(
    'retains text-%s when a component sets a semantic colour', size => {
      expect(cn(`text-${size}`, 'text-[var(--color-foreground)]')).toBe(
        `text-${size} text-[var(--color-foreground)]`,
      )
    },
  )
  it('lets callers override font size without removing text colour', () => {
    expect(cn('text-heading text-[var(--color-foreground)]', 'text-sm')).toBe(
      'text-[var(--color-foreground)] text-sm',
    )
    expect(cn('text-sm', 'text-caption text-[var(--color-danger)]')).toBe(
      'text-caption text-[var(--color-danger)]',
    )
  })
})

// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest'
import { render, cleanup, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'

import PresenceDot from './PresenceDot'

afterEach(() => cleanup())

describe('PresenceDot', () => {
  it('keeps the inline dot filled and whisper-bordered by default', () => {
    render(<PresenceDot online={false} />)
    const dot = screen.getByRole('status')
    expect(dot.style.background).toBe('var(--color-foreground-subtle)')
    expect(dot.style.boxShadow).not.toContain('--presence-ring')
  })

  it('cuts the dot out of its surface with a --presence-ring halo when ringed', () => {
    render(<PresenceDot online ring />)
    const dot = screen.getByRole('status')
    expect(dot.style.background).toBe('var(--color-status-online, #5b9e6d)')
    expect(dot.style.boxShadow).toContain('var(--presence-ring, var(--color-surface))')
  })

  it('renders a ringed offline dot hollow so state is not carried by color alone', () => {
    render(<PresenceDot online={false} ring />)
    const dot = screen.getByRole('status')
    expect(dot.dataset.presence).toBe('offline')
    expect(dot.style.background).toBe('var(--presence-ring, var(--color-surface))')
    expect(dot.style.boxShadow).toContain('inset')
    expect(dot.style.boxShadow).toContain('var(--color-foreground-subtle)')
  })

  it('marks a working agent with a pulse ring and says so in its name [MSG-09]', () => {
    render(<PresenceDot online ring working variant="agent" />)
    const dot = screen.getByRole('status')
    expect(dot.dataset.working).toBe('true')
    expect(dot.className).toContain('presence-working')
    expect(dot.getAttribute('aria-label')).toBe('Online · Responding')
  })

  it('leaves an idle dot without the pulse ring [MSG-09]', () => {
    render(<PresenceDot online ring />)
    const dot = screen.getByRole('status')
    expect(dot.dataset.working).toBeUndefined()
    expect(dot.className).not.toContain('presence-working')
  })
})

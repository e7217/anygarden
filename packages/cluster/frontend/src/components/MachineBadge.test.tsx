// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import MachineBadge from './MachineBadge'

const remote = {
  machine_id: 'm-remote',
  machine_name: 'anygarden-svc',
  machine_hostname: 'svc-host',
  is_local: false,
}

afterEach(() => cleanup())

describe('MachineBadge', () => {
  it('[NODE-15] renders nothing for an unplaced agent', () => {
    render(<MachineBadge placement={null} online />)
    expect(screen.queryByTestId('agent-machine-badge')).toBeNull()
  })

  it('[NODE-15] renders nothing for an agent on this node', () => {
    render(<MachineBadge placement={{ ...remote, is_local: true }} online />)
    expect(screen.queryByTestId('agent-machine-badge')).toBeNull()
  })

  it('[NODE-15] names the remote machine and its host', () => {
    render(<MachineBadge placement={remote} online />)
    const badge = screen.getByTestId('agent-machine-badge')
    expect(badge).toHaveTextContent('anygarden-svc')
    expect(badge).toHaveAttribute('data-online', 'true')
    expect(badge.getAttribute('title')).toContain('anygarden-svc')
    expect(badge.getAttribute('title')).toContain('svc-host')
    expect(badge.getAttribute('title')).toContain('online')
  })

  it('[NODE-15] marks an offline remote machine', () => {
    render(<MachineBadge placement={remote} online={false} />)
    const badge = screen.getByTestId('agent-machine-badge')
    expect(badge).toHaveAttribute('data-online', 'false')
    expect(badge.getAttribute('title')).toContain('offline')
  })
})

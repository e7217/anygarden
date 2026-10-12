// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'

vi.mock('@xyflow/react', () => {
  return {
    Handle: ({ type }: { type: string }) => (
      <span data-testid={`handle-${type}`} />
    ),
    Position: { Top: 'top', Bottom: 'bottom' },
  }
})

import { PeerNode } from './PeerNode'

afterEach(() => cleanup())

describe('PeerNode', () => {
  const renderNode = (data: Record<string, unknown>, selected = false) =>
    render(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      <PeerNode data={data} selected={selected} {...({} as any)} />,
    )

  it('[FED-09] renders the peer node id and its trust state as text', () => {
    renderNode({
      label: '11111111-1111-1111-1111-111111111111',
      node_id: '11111111-1111-1111-1111-111111111111',
      state: 'active',
      channel_count: 2,
    })
    expect(screen.getByText('11111111-1111-1111-1111-111111111111')).toBeInTheDocument()
    expect(screen.getByText('Trusted · 2 shared channels')).toBeInTheDocument()
    const node = screen.getByLabelText(/Federation peer 11111111-1111-1111-1111-111111111111, trust Trusted/)
    expect(node).toHaveAttribute('data-peer-state', 'active')
  })

  it('[FED-09] shows revoked trust as text, not only color', () => {
    renderNode({ label: 'peer-b', node_id: 'peer-b', state: 'revoked', channel_count: 0 })
    expect(screen.getByText('Revoked · 0 shared channels')).toBeInTheDocument()
    expect(screen.getByLabelText(/trust Revoked/)).toHaveAttribute('data-peer-state', 'revoked')
  })

  it('[FED-09] falls back to the raw state for unknown values', () => {
    renderNode({ label: 'peer-c', state: 'pending', channel_count: 1 })
    expect(screen.getByText('Pending · 1 shared channel')).toBeInTheDocument()
    renderNode({ label: 'peer-d', state: 'quarantined' })
    expect(screen.getByText('quarantined · 0 shared channels')).toBeInTheDocument()
  })
})

// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'

// Keep @lobehub/icons out of the unit-test bundle.
vi.mock('@/components/EngineGlyph', () => ({
  EngineGlyph: ({ engine }: { engine?: string }) => <svg data-testid={`engine-${engine}`} />,
}))

const agentsMock = vi.hoisted(() => ({
  agents: [
    {
      id: 'in', name: 'agent01', engine: 'codex-cli',
      actual_state: 'running', machine_online: true,
      avatar_kind: 'emoji', avatar_value: '🤖',
    },
    {
      id: 'out', name: 'wiki', engine: 'claude-code',
      actual_state: 'stopped', machine_online: true,
    },
  ],
  addAgentToRoom: vi.fn(),
  removeAgentFromRoom: vi.fn(),
}))
vi.mock('@/hooks/useAgents', () => ({ useAgents: () => agentsMock }))

import ManageRoomAgentsDialog from './ManageRoomAgentsDialog'

afterEach(() => cleanup())

describe('ManageRoomAgentsDialog — agent rows (#869)', () => {
  it('shows each agent avatar with its presence overlaid instead of a generic icon and loose dot', () => {
    render(
      <ManageRoomAgentsDialog
        open
        onOpenChange={() => {}}
        roomId="r1"
        participantAgentIds={new Set(['in'])}
        onChange={() => {}}
      />,
    )

    expect(screen.getByText('🤖')).toBeInTheDocument()
    expect(screen.getByTestId('engine-codex-cli')).toBeInTheDocument()
    expect(screen.getByTestId('engine-claude-code')).toBeInTheDocument()

    const dots = screen.getAllByRole('status')
    expect(dots).toHaveLength(2)
    for (const dot of dots) {
      expect(dot).toHaveAttribute('data-testid', 'entity-avatar-presence')
    }
    expect(dots.map(d => d.dataset.presence)).toEqual(['online', 'offline'])
  })
})

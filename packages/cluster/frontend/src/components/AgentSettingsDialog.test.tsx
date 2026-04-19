// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter } from 'react-router-dom'

vi.mock('@/components/EngineGlyph', () => ({
  EngineGlyph: ({ engine }: { engine: string | undefined }) => (
    <svg data-testid={`engine-${engine ?? 'none'}`} />
  ),
}))

import AgentSettingsDialog from './AgentSettingsDialog'
import type { Agent } from '@/hooks/useAgents'

afterEach(() => cleanup())

function makeAgent(overrides: Partial<Agent> = {}): Agent {
  return {
    id: 'a1',
    name: 'bot',
    engine: 'claude-code',
    desired_state: 'running',
    actual_state: 'online',
    restart_policy: 'always',
    agents_md: null,
    ...overrides,
  }
}

function setup(open: boolean = true) {
  const updateAgent = vi.fn().mockResolvedValue(makeAgent())
  const fetchAgentFiles = vi.fn().mockResolvedValue([])
  const upsertAgentFile = vi.fn()
  const deleteAgentFile = vi.fn()
  const onOpenChange = vi.fn()
  render(
    <MemoryRouter>
      <AgentSettingsDialog
        agent={makeAgent()}
        open={open}
        onOpenChange={onOpenChange}
        fetchAgentFiles={fetchAgentFiles}
        updateAgent={updateAgent}
        upsertAgentFile={upsertAgentFile}
        deleteAgentFile={deleteAgentFile}
      />
    </MemoryRouter>,
  )
  return { updateAgent, fetchAgentFiles, onOpenChange }
}

describe('AgentSettingsDialog', () => {
  it('renders four nav items and opens on the Overview section by default', () => {
    setup()
    expect(screen.getByTestId('agent-settings-nav-overview')).toHaveAttribute(
      'aria-current',
      'page',
    )
    expect(screen.getByTestId('agent-settings-nav-manifest')).toBeInTheDocument()
    expect(screen.getByTestId('agent-settings-nav-rooms')).toBeInTheDocument()
    expect(screen.getByTestId('agent-settings-nav-activity')).toBeInTheDocument()
    expect(screen.getByTestId('overview-panel')).toBeInTheDocument()
  })

  it('switches the right pane when a different nav item is clicked', async () => {
    setup()
    fireEvent.click(screen.getByTestId('agent-settings-nav-manifest'))
    expect(
      await screen.findByTestId('agent-settings-nav-manifest'),
    ).toHaveAttribute('aria-current', 'page')
    expect(screen.queryByTestId('overview-panel')).toBeNull()
    expect(await screen.findByTestId('manifest-panel')).toBeInTheDocument()
  })

  it('does not render any panel content when closed', () => {
    setup(false)
    expect(screen.queryByTestId('overview-panel')).toBeNull()
    expect(screen.queryByTestId('manifest-panel')).toBeNull()
  })
})

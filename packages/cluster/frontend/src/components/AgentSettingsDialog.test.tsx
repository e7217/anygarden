// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter } from 'react-router-dom'

vi.mock('@/components/EngineGlyph', () => ({
  EngineGlyph: ({ engine }: { engine: string | undefined }) => (
    <svg data-testid={`engine-${engine ?? 'none'}`} />
  ),
}))

// The single-page dialog mounts every panel simultaneously (#165),
// so the Activity and Rooms panels fire their data-fetch effects on
// render. jsdom's URL parser chokes on relative paths in
// ``fetch(/api/...)``, so stub apiFetch to resolve with empty
// payloads — these tests don't assert on panel contents.
vi.mock('@/lib/api', () => ({
  apiFetch: vi.fn().mockResolvedValue({
    ok: true,
    json: async () => [],
  }),
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
  it('stacks all four sections vertically when open', async () => {
    setup()
    // Each section has its own aria-labelled wrapper so the admin
    // can scan the whole agent without navigating.
    expect(screen.getByTestId('agent-settings-section-overview')).toBeInTheDocument()
    expect(screen.getByTestId('agent-settings-section-manifest')).toBeInTheDocument()
    expect(screen.getByTestId('agent-settings-section-rooms')).toBeInTheDocument()
    expect(screen.getByTestId('agent-settings-section-activity')).toBeInTheDocument()

    // All panels are mounted simultaneously — Manifest edits survive
    // scrolling to other sections because no section unmounts.
    expect(screen.getByTestId('overview-panel')).toBeInTheDocument()
    expect(await screen.findByTestId('manifest-panel')).toBeInTheDocument()
    expect(screen.getByTestId('rooms-panel')).toBeInTheDocument()
    expect(screen.getByTestId('activity-panel')).toBeInTheDocument()
  })

  it('renders section headings in document order (Overview → Manifest → Rooms → Activity)', () => {
    setup()
    const sections = screen.getAllByRole('region')
    const labels = sections.map(s =>
      (s.getAttribute('aria-labelledby') ?? '').replace(
        'agent-settings-heading-',
        '',
      ),
    )
    expect(labels).toEqual(['overview', 'manifest', 'rooms', 'activity'])
  })

  it('does not render any panel content when closed', () => {
    setup(false)
    expect(screen.queryByTestId('overview-panel')).toBeNull()
    expect(screen.queryByTestId('manifest-panel')).toBeNull()
    expect(screen.queryByTestId('rooms-panel')).toBeNull()
    expect(screen.queryByTestId('activity-panel')).toBeNull()
  })
})

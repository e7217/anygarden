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
  // #217 — Overview panel now consumes this to populate Model/Reasoning
  // dropdowns. ``null`` keeps the rows hidden so this suite (which
  // asserts section presence, not dropdown wiring) stays focused.
  const fetchEngineCatalog = vi.fn().mockResolvedValue(null)
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
        fetchEngineCatalog={fetchEngineCatalog}
      />
    </MemoryRouter>,
  )
  return { updateAgent, fetchAgentFiles, fetchEngineCatalog, onOpenChange }
}

describe('AgentSettingsDialog', () => {
  it('stacks all four sections vertically when open', async () => {
    setup()
    expect(screen.getByTestId('agent-settings-section-overview')).toBeInTheDocument()
    expect(screen.getByTestId('agent-settings-section-manifest')).toBeInTheDocument()
    expect(screen.getByTestId('agent-settings-section-rooms')).toBeInTheDocument()
    expect(screen.getByTestId('agent-settings-section-activity')).toBeInTheDocument()

    // All panels are mounted simultaneously — Manifest edits survive
    // scrolling to other sections because no section unmounts. The
    // Activity panel is mounted but hidden inside a collapsed
    // `<details>`; the DOM node is still present.
    expect(screen.getByTestId('overview-panel')).toBeInTheDocument()
    expect(await screen.findByTestId('manifest-panel')).toBeInTheDocument()
    expect(screen.getByTestId('rooms-panel')).toBeInTheDocument()
    expect(screen.getByTestId('activity-panel')).toBeInTheDocument()
  })

  it('renders sections in document order (Overview → Manifest → Rooms → Activity)', () => {
    setup()
    const ids = [
      'agent-settings-section-overview',
      'agent-settings-section-manifest',
      'agent-settings-section-rooms',
      'agent-settings-section-activity',
    ]
    const nodes = ids.map(id => screen.getByTestId(id))
    // Walk the pairs with compareDocumentPosition and require each
    // later node to follow the previous one in the DOM.
    for (let i = 0; i < nodes.length - 1; i++) {
      const relation = nodes[i].compareDocumentPosition(nodes[i + 1])
      expect(relation & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    }
  })

  it('collapses the Activity section by default (admin can expand on demand)', () => {
    setup()
    const activitySection = screen.getByTestId('agent-settings-section-activity') as HTMLDetailsElement
    expect(activitySection.tagName.toLowerCase()).toBe('details')
    expect(activitySection.open).toBe(false)
  })

  it('does not render any panel content when closed', () => {
    setup(false)
    expect(screen.queryByTestId('overview-panel')).toBeNull()
    expect(screen.queryByTestId('manifest-panel')).toBeNull()
    expect(screen.queryByTestId('rooms-panel')).toBeNull()
    expect(screen.queryByTestId('activity-panel')).toBeNull()
  })
})

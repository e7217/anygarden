// @vitest-environment jsdom
import { useMemo, useState } from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
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

function setup(open: boolean = true, agent: Agent = makeAgent()) {
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
        agent={agent}
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

  it('opens the activity log from the section navigation without unmounting edits', async () => {
    setup()
    const name = screen.getByTestId('overview-name-input') as HTMLInputElement
    fireEvent.change(name, { target: { value: 'Draft name' } })
    fireEvent.click(screen.getByRole('button', { name: 'Activity' }))
    expect((screen.getByTestId('agent-settings-section-activity') as HTMLDetailsElement).open).toBe(true)
    expect(name).toHaveValue('Draft name')
    expect(await screen.findByTestId('manifest-panel')).toBeInTheDocument()
    expect(screen.getByTestId('workspace-panel')).toBeInTheDocument()
  })

  it('does not render any panel content when closed', () => {
    setup(false)
    expect(screen.queryByTestId('overview-panel')).toBeNull()
    expect(screen.queryByTestId('manifest-panel')).toBeNull()
    expect(screen.queryByTestId('rooms-panel')).toBeNull()
    expect(screen.queryByTestId('activity-panel')).toBeNull()
  })

  it('marks the header presence dot offline when machine_online is false', () => {
    setup(true, makeAgent({ actual_state: 'running', machine_online: false }))
    expect(screen.getAllByLabelText('Offline · unreachable').length).toBeGreaterThan(0)
  })
})

// Issue #281 — the parent components that mount this dialog (Sidebar
// and AdminMachines) used to hold the displayed agent as a snapshot
// (``useState<Agent | null>``). After an in-dialog edit triggered
// ``updateAgent → fetchAgents``, the canonical agents list updated but
// the snapshot did not, leaving the dialog with stale prop values
// until it was closed and reopened.
//
// The canonical pattern keeps only the agent ID in state and derives
// the Agent object from the agents list every render. This block
// documents and exercises that pattern: callers are expected to follow
// the same shape, and a future regression on either side is caught
// here as long as the wrapper continues to mirror the call sites.
describe('AgentSettingsDialog — parent state pattern (#281)', () => {
  it('keeps the name in sync after the agents list mutates', async () => {

    function Parent() {
      const initial = makeAgent({ id: 'a1', name: 'before' })
      const [agents, setAgents] = useState<Agent[]>([initial])
      // Canonical pattern — store the open agent's ID, derive the
      // Agent from the live list so it tracks ``setAgents`` updates.
      const [openId] = useState<string | null>(initial.id)
      const settingsAgent = useMemo(
        () => (openId ? agents.find(a => a.id === openId) ?? null : null),
        [agents, openId],
      )

      // Simulates ``updateAgent → fetchAgents`` resolving with a new
      // list — the agent record acquires a different ``model`` value.
      const mutate = () =>
        setAgents(prev =>
          prev.map(a =>
            a.id === openId ? { ...a, name: 'after' } : a,
          ),
        )

      return (
        <>
          <button data-testid="mutate-agents" onClick={mutate}>
            mutate
          </button>
          <AgentSettingsDialog
            agent={settingsAgent}
            open={true}
            onOpenChange={() => {}}
            fetchAgentFiles={vi.fn().mockResolvedValue([])}
            updateAgent={vi.fn()}
            upsertAgentFile={vi.fn()}
            deleteAgentFile={vi.fn()}
            fetchEngineCatalog={vi.fn().mockResolvedValue(null)}
          />
        </>
      )
    }

    render(
      <MemoryRouter>
        <Parent />
      </MemoryRouter>,
    )

    const input = screen.getByTestId('overview-name-input') as HTMLInputElement
    expect(input.value).toBe('before')
    fireEvent.click(screen.getByTestId('mutate-agents'))
    expect(input.value).toBe('after')
  })
})

// Issue #435 — option parity with AgentSettingsMenu. The unified dialog
// previously lacked the per-agent admin actions (delete, context-window
// opt-out) the row menu carried, so the available actions differed by
// entry point. A footer now mirrors the menu's "show-when-permitted"
// handlers.
describe('AgentSettingsDialog — option parity footer (#435)', () => {
  it('hides the footer when no delete/toggle handlers are supplied', () => {
    setup()
    expect(screen.queryByTestId('agent-settings-delete')).toBeNull()
    expect(
      screen.queryByTestId('agent-settings-context-window-opt-out'),
    ).toBeNull()
  })

  it('surfaces Delete and the context-window toggle at parity with the row menu', () => {
    const onDelete = vi.fn()
    const onToggle = vi.fn()
    render(
      <MemoryRouter>
        <AgentSettingsDialog
          agent={makeAgent()}
          open
          onOpenChange={vi.fn()}
          fetchAgentFiles={vi.fn().mockResolvedValue([])}
          updateAgent={vi.fn().mockResolvedValue(makeAgent())}
          upsertAgentFile={vi.fn()}
          deleteAgentFile={vi.fn()}
          fetchEngineCatalog={vi.fn().mockResolvedValue(null)}
          onDelete={onDelete}
          contextWindowOptOut={false}
          onToggleContextWindowOptOut={onToggle}
        />
      </MemoryRouter>,
    )
    const del = screen.getByTestId('agent-settings-delete')
    expect(del).toBeInTheDocument()
    expect(del.className).toMatch(/text-\[var\(--color-destructive\)\]/)
    fireEvent.click(del)
    expect(onDelete).toHaveBeenCalledTimes(1)

    const toggle = screen.getByTestId('agent-settings-context-window-opt-out')
    expect(toggle).toHaveAttribute('aria-checked', 'false')
    fireEvent.click(toggle)
    expect(onToggle).toHaveBeenCalledTimes(1)
  })

  it('checks the toggle when contextWindowOptOut is true', () => {
    render(
      <MemoryRouter>
        <AgentSettingsDialog
          agent={makeAgent()}
          open
          onOpenChange={vi.fn()}
          fetchAgentFiles={vi.fn().mockResolvedValue([])}
          updateAgent={vi.fn().mockResolvedValue(makeAgent())}
          upsertAgentFile={vi.fn()}
          deleteAgentFile={vi.fn()}
          fetchEngineCatalog={vi.fn().mockResolvedValue(null)}
          contextWindowOptOut={true}
          onToggleContextWindowOptOut={vi.fn()}
        />
      </MemoryRouter>,
    )
    expect(
      screen.getByTestId('agent-settings-context-window-opt-out'),
    ).toHaveAttribute('aria-checked', 'true')
  })
})

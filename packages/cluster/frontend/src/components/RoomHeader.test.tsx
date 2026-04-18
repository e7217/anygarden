// @vitest-environment jsdom
// RoomHeader / RoomSettingsMenu contract tests.
// The parent (ChatPage) relies on a stable rule — "prop is undefined
// ⇒ the corresponding UI element is hidden" — to gate controls by
// permission / context (#116 agent-DM guards being the latest caller).
// These tests lock the contract in so that a future refactor of
// either component surfaces a failure instead of silently re-enabling
// hidden controls.
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter } from 'react-router-dom'
import RoomHeader from './RoomHeader'

// EntityAvatar pulls in @lobehub/icons which is heavy and unrelated
// to the contract being exercised here; swap it for a stub so the
// suite focuses on DOM-level visibility of controls.
vi.mock('@/components/EntityAvatar', () => ({
  EntityAvatar: () => null,
}))

afterEach(() => cleanup())

function renderHeader(
  overrides: Partial<React.ComponentProps<typeof RoomHeader>> = {},
) {
  const base: React.ComponentProps<typeof RoomHeader> = {
    roomName: 'general',
    connected: true,
    participantCount: 3,
    agentsOnline: 1,
    agentsTotal: 2,
    representativeAgentId: 'a1',
    agentParticipants: [
      { id: 'p1', agent_id: 'a1', display_name: 'Agent 1', online: true },
      { id: 'p2', agent_id: 'a2', display_name: 'Agent 2', online: false },
    ],
    isDm: false,
    onSetRepresentative: vi.fn(),
    onManageAgents: vi.fn(),
    onCreateSubRoom: vi.fn(),
    onEditRoom: vi.fn(),
    onManageInvites: vi.fn(),
    onStopAllAgents: vi.fn(),
    onDeleteRoom: vi.fn(),
    onOpenSidebar: vi.fn(),
    onToggleParticipants: vi.fn(),
  }
  return render(
    <MemoryRouter>
      <RoomHeader {...base} {...overrides} />
    </MemoryRouter>,
  )
}

function openSettingsMenu() {
  fireEvent.click(screen.getByTestId('room-header-settings-menu-trigger'))
}

describe('RoomHeader — full controls baseline', () => {
  it('renders every control when all handlers are provided', () => {
    renderHeader()
    expect(screen.getByTestId('room-header-participants-toggle')).toBeInTheDocument()
    expect(screen.getByTestId('room-header-agent-liveness')).toBeInTheDocument()
    // representative select is the only <select> in the header
    expect(screen.getByTitle('Set representative agent')).toBeInTheDocument()
    // overflow trigger is always rendered as long as at least one
    // action handler is supplied — verify by opening it below.
    openSettingsMenu()
    expect(screen.getByTestId('room-menu-new-sub-room')).toBeInTheDocument()
    expect(screen.getByTestId('room-menu-edit')).toBeInTheDocument()
    expect(screen.getByTestId('room-menu-invites')).toBeInTheDocument()
    expect(screen.getByTestId('room-menu-agents')).toBeInTheDocument()
    expect(screen.getByTestId('room-menu-stop-all')).toBeInTheDocument()
    expect(screen.getByTestId('room-menu-delete')).toBeInTheDocument()
  })
})

describe('RoomHeader — undefined-prop hides the corresponding control', () => {
  it('hides the participants toggle when participantCount is undefined', () => {
    renderHeader({ participantCount: undefined, onToggleParticipants: undefined })
    expect(screen.queryByTestId('room-header-participants-toggle')).toBeNull()
  })

  it('hides the agents N/N badge when agentsTotal or agentsOnline is undefined', () => {
    renderHeader({ agentsTotal: undefined, agentsOnline: undefined })
    expect(screen.queryByTestId('room-header-agent-liveness')).toBeNull()
  })

  it('hides the representative select when onSetRepresentative is undefined', () => {
    renderHeader({ onSetRepresentative: undefined })
    expect(screen.queryByTitle('Set representative agent')).toBeNull()
  })

  it('hides the representative select when agentParticipants is empty', () => {
    renderHeader({ agentParticipants: [] })
    expect(screen.queryByTitle('Set representative agent')).toBeNull()
  })
})

describe('RoomSettingsMenu — menu items follow the undefined-hide contract', () => {
  it('omits non-applicable actions but keeps destructive ones when admin/owner', () => {
    // Mirrors what ChatPage will pass for an agent-DM (#116): no
    // sub-room / edit / invites / manage-agents, but still StopAll
    // (admin) and Delete room (owner).
    renderHeader({
      onCreateSubRoom: undefined,
      onEditRoom: undefined,
      onManageInvites: undefined,
      onManageAgents: undefined,
      // Keep destructive handlers wired.
    })
    openSettingsMenu()
    expect(screen.queryByTestId('room-menu-new-sub-room')).toBeNull()
    expect(screen.queryByTestId('room-menu-edit')).toBeNull()
    expect(screen.queryByTestId('room-menu-invites')).toBeNull()
    expect(screen.queryByTestId('room-menu-agents')).toBeNull()
    // Destructive row stays.
    expect(screen.getByTestId('room-menu-stop-all')).toBeInTheDocument()
    expect(screen.getByTestId('room-menu-delete')).toBeInTheDocument()
  })

  it('renders no overflow trigger when every action handler is undefined', () => {
    renderHeader({
      onCreateSubRoom: undefined,
      onEditRoom: undefined,
      onManageInvites: undefined,
      onManageAgents: undefined,
      onStopAllAgents: undefined,
      onDeleteRoom: undefined,
    })
    expect(screen.queryByTestId('room-header-settings-menu-trigger')).toBeNull()
  })
})

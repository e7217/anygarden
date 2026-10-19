// @vitest-environment jsdom
// RoomHeader / RoomSettingsMenu contract tests.
// The parent (ChatPage) relies on a stable rule — "prop is undefined
// ⇒ the corresponding UI element is hidden" — to gate controls by
// permission / context (#116 agent-DM guards being the latest caller).
// These tests lock the contract in so that a future refactor of
// either component surfaces a failure instead of silently re-enabling
// hidden controls.
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter } from 'react-router-dom'
import RoomHeader from './RoomHeader'

// EntityAvatar pulls in @lobehub/icons which is heavy and unrelated
// to the contract being exercised here; swap it for a stub so the
// suite focuses on DOM-level visibility of controls.
vi.mock('@/components/EntityAvatar', () => ({
  EntityAvatar: () => null,
}))

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

function renderHeader(
  overrides: Partial<React.ComponentProps<typeof RoomHeader>> = {},
) {
  const base: React.ComponentProps<typeof RoomHeader> = {
    roomName: 'general',
    connected: true,
    participantCount: 3,
    agentsOnline: 1,
    agentsTotal: 2,
    isDm: false,
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
})

describe('RoomHeader — only signals that need attention (#897)', () => {
  it('[ROOM-10] keeps the representative pick and the agent count out of the header', () => {
    renderHeader()
    expect(screen.queryByTestId('room-header-agent-liveness')).toBeNull()
    expect(screen.queryByRole('combobox')).toBeNull()
  })

  it('[ROOM-10] shows no connection badge while connected', () => {
    renderHeader({ connected: true })
    expect(screen.queryByTestId('room-header-connection')).toBeNull()
  })

  it('[ROOM-10] shows the disconnected badge only once the outage outlasts the grace period', () => {
    vi.useFakeTimers()
    renderHeader({ connected: false })
    expect(screen.queryByTestId('room-header-connection')).toBeNull()
    act(() => {
      vi.advanceTimersByTime(2000)
    })
    expect(screen.getByTestId('room-header-connection')).toHaveTextContent('Disconnected')
  })

  it('hides the badge again when the connection comes back', () => {
    vi.useFakeTimers()
    const { rerender } = renderHeader({ connected: false })
    act(() => {
      vi.advanceTimersByTime(2000)
    })
    expect(screen.getByTestId('room-header-connection')).toBeInTheDocument()
    rerender(
      <MemoryRouter>
        <RoomHeader roomName="general" connected participantCount={3} />
      </MemoryRouter>,
    )
    expect(screen.queryByTestId('room-header-connection')).toBeNull()
  })

  it('[ROOM-10] marks the participants button when an agent is offline', () => {
    renderHeader({ agentsOnline: 1, agentsTotal: 2 })
    const toggle = screen.getByTestId('room-header-participants-toggle')
    expect(toggle).toHaveAccessibleName(/1 agent offline/)
    expect(screen.getByTestId('room-header-agents-offline')).toBeInTheDocument()
  })

  it('leaves the participants button plain when every agent is online', () => {
    renderHeader({ agentsOnline: 2, agentsTotal: 2 })
    const toggle = screen.getByTestId('room-header-participants-toggle')
    expect(toggle).not.toHaveAccessibleName(/offline/)
    expect(screen.queryByTestId('room-header-agents-offline')).toBeNull()
  })
})

describe('RoomSettingsMenu — menu items follow the undefined-hide contract', () => {
  it('opens workspace access only when the room grants a management handler', () => {
    const onManageWorkspaces = vi.fn()
    const { unmount } = renderHeader({ onManageWorkspaces })
    openSettingsMenu()
    fireEvent.click(screen.getByTestId('room-menu-workspaces'))
    expect(onManageWorkspaces).toHaveBeenCalledOnce()
    expect(screen.queryByTestId('room-menu-workspaces')).toBeNull()
    unmount()
    renderHeader()
    openSettingsMenu()
    expect(screen.queryByTestId('room-menu-workspaces')).toBeNull()
  })

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

// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter } from 'react-router-dom'
import Sidebar from './Sidebar'

// Sidebar pulls in a chain of provider-backed hooks. For the
// collapse/expand behaviour (#106/#115) we only care about the root
// ``<aside>`` class & header button, so stub the data hooks out
// with minimal shapes. Each mock mirrors the live shape just
// enough for the component to render without throwing.
const authMockState = vi.hoisted(() => ({
  isAdmin: false,
  logout: vi.fn(),
}))
const roomsMockState = vi.hoisted(() => ({
  projects: [] as Array<{ id: string; name: string }>,
  rooms: {} as Record<string, Array<{
    id: string
    name: string
    project_id: string | null
    is_dm: boolean
    parent_room_id?: string | null
    representative_agent_id?: string | null
    has_updates?: boolean
  }>>,
  agentDMs: [] as Array<{
    id: string
    name: string
    project_id: string | null
    is_dm: boolean
    representative_agent_id?: string | null
    has_updates?: boolean
  }>,
  createProject: vi.fn(),
  deleteProject: vi.fn(),
  createRoom: vi.fn(),
  fetchRooms: vi.fn(),
  fetchAgentDMs: vi.fn(),
  pinRoom: vi.fn(),
  reorderPinnedRooms: vi.fn(),
  createAgentDM: vi.fn(),
  setRoomEphemeral: vi.fn(),
  markRoomRead: vi.fn(),
}))
const agentsMockState = vi.hoisted(() => ({
  agents: [] as Array<{
    id: string
    name: string
    engine: string
    desired_state: string
    actual_state: string
    restart_policy: string
    machine_online?: boolean
    context_window_opt_out?: boolean
  }>,
  deleteAgent: vi.fn(),
  updateAgent: vi.fn(),
  fetchAgentFiles: vi.fn(),
  upsertAgentFile: vi.fn(),
  deleteAgentFile: vi.fn(),
  fetchAttachedSkills: vi.fn(),
  fetchSkillPreview: vi.fn(),
  fetchEngineCatalog: vi.fn(),
}))

vi.mock('@/hooks/useAuth', () => ({
  useAuth: () => ({
    user: { id: 'u1', email: 'u@example.com', is_admin: authMockState.isAdmin },
    logout: authMockState.logout,
  }),
}))
vi.mock('@/hooks/useAgents', () => ({
  useAgents: () => ({
    agents: agentsMockState.agents,
    deleteAgent: agentsMockState.deleteAgent,
    updateAgent: agentsMockState.updateAgent,
    fetchAgentFiles: agentsMockState.fetchAgentFiles,
    upsertAgentFile: agentsMockState.upsertAgentFile,
    deleteAgentFile: agentsMockState.deleteAgentFile,
    fetchAttachedSkills: agentsMockState.fetchAttachedSkills,
    fetchSkillPreview: agentsMockState.fetchSkillPreview,
    fetchEngineCatalog: agentsMockState.fetchEngineCatalog,
  }),
}))
vi.mock('@/hooks/useRooms', () => ({
  useRooms: () => ({
    projects: roomsMockState.projects,
    rooms: roomsMockState.rooms,
    agentDMs: roomsMockState.agentDMs,
    status: 'ready',
    fetchProjects: vi.fn(),
    refetch: vi.fn(),
    createProject: roomsMockState.createProject,
    deleteProject: roomsMockState.deleteProject,
    createRoom: roomsMockState.createRoom,
    fetchRooms: roomsMockState.fetchRooms,
    fetchAgentDMs: roomsMockState.fetchAgentDMs,
    pinRoom: roomsMockState.pinRoom,
    reorderPinnedRooms: roomsMockState.reorderPinnedRooms,
    createAgentDM: roomsMockState.createAgentDM,
    setRoomEphemeral: roomsMockState.setRoomEphemeral,
    markRoomRead: roomsMockState.markRoomRead,
  }),
}))

// #115 — Sidebar now reads collapse state from useSidebarLayout.
// Mock the module the same way useRooms/useAuth are mocked; each
// test controls the returned collapsed/toggleCollapsed via the
// ``mockReturnValue`` below.
const toggleCollapsedSpy = vi.fn()
const mockSidebarLayout = vi.fn(() => ({
  collapsed: false,
  toggleCollapsed: toggleCollapsedSpy,
  setCollapsed: vi.fn(),
}))
vi.mock('@/hooks/useSidebarLayout', () => ({
  useSidebarLayout: () => mockSidebarLayout(),
}))

// Stub EntityAvatar to avoid pulling in @lobehub/ui transitively
// — the suite is focused on layout/props for the collapse feature,
// avatar rendering is covered by EntityAvatar.test.tsx.
vi.mock('@/components/EntityAvatar', () => ({
  EntityAvatar: () => null,
}))

beforeEach(() => {
  authMockState.isAdmin = false
  authMockState.logout.mockReset()
  roomsMockState.projects = []
  roomsMockState.rooms = {}
  roomsMockState.agentDMs = []
  roomsMockState.createProject.mockReset()
  roomsMockState.deleteProject.mockReset()
  roomsMockState.createRoom.mockReset()
  roomsMockState.fetchRooms.mockReset()
  roomsMockState.fetchAgentDMs.mockReset()
  roomsMockState.pinRoom.mockReset()
  roomsMockState.reorderPinnedRooms.mockReset()
  roomsMockState.createAgentDM.mockReset()
  roomsMockState.setRoomEphemeral.mockReset()
  roomsMockState.markRoomRead.mockReset()
  agentsMockState.agents = []
  agentsMockState.deleteAgent.mockReset()
  agentsMockState.updateAgent.mockReset()
  agentsMockState.fetchAgentFiles.mockReset()
  agentsMockState.upsertAgentFile.mockReset()
  agentsMockState.deleteAgentFile.mockReset()
  agentsMockState.fetchAttachedSkills.mockReset()
  agentsMockState.fetchSkillPreview.mockReset()
  agentsMockState.fetchEngineCatalog.mockReset()
  localStorage.clear()
  toggleCollapsedSpy.mockReset()
  mockSidebarLayout.mockReset()
  mockSidebarLayout.mockReturnValue({
    collapsed: false,
    toggleCollapsed: toggleCollapsedSpy,
    setCollapsed: vi.fn(),
  })
})

afterEach(() => cleanup())

function renderSidebar() {
  return render(
    <MemoryRouter>
      <Sidebar selectedRoom={null} />
    </MemoryRouter>,
  )
}

describe('Sidebar — experimental admin nav badges (#346)', () => {
  it('marks LLM Gateway and Topology as experimental entries', () => {
    authMockState.isAdmin = true

    renderSidebar()

    expect(screen.getByRole('button', {
      name: 'LLM Gateway, experimental feature',
    })).toBeInTheDocument()
    expect(screen.getByRole('button', {
      name: 'Topology, experimental feature',
    })).toBeInTheDocument()
    expect(screen.getAllByText('Experimental')).toHaveLength(2)
  })
})

describe('Sidebar — update indicators (#385)', () => {
  it('renders a dot beside project rooms with unread updates', () => {
    localStorage.setItem('anygarden_expanded_projects', JSON.stringify(['p1']))
    roomsMockState.projects = [{ id: 'p1', name: 'Project One' }]
    roomsMockState.rooms = {
      p1: [
        {
          id: 'r1',
          name: 'General',
          project_id: 'p1',
          is_dm: false,
          has_updates: true,
        },
      ],
    }

    renderSidebar()

    expect(screen.getByTestId('sidebar-room-r1')).toBeInTheDocument()
    expect(screen.getByLabelText('읽지 않은 업데이트 있음')).toBeInTheDocument()
  })

  it('omits the dot when a room has no unread updates', () => {
    localStorage.setItem('anygarden_expanded_projects', JSON.stringify(['p1']))
    roomsMockState.projects = [{ id: 'p1', name: 'Project One' }]
    roomsMockState.rooms = {
      p1: [
        {
          id: 'r1',
          name: 'General',
          project_id: 'p1',
          is_dm: false,
          has_updates: false,
        },
      ],
    }

    renderSidebar()

    expect(screen.queryByLabelText('읽지 않은 업데이트 있음')).not.toBeInTheDocument()
  })

  it('aggregates DM update dots on collapsed multi-DM agent rows', () => {
    authMockState.isAdmin = true
    agentsMockState.agents = [
      {
        id: 'a1',
        name: 'Agent One',
        engine: 'claude-code',
        desired_state: 'running',
        actual_state: 'running',
        restart_policy: 'never',
        machine_online: true,
      },
    ]
    roomsMockState.agentDMs = [
      {
        id: 'dm1',
        name: 'DM: Agent One',
        project_id: null,
        is_dm: true,
        representative_agent_id: 'a1',
        has_updates: true,
      },
      {
        id: 'dm2',
        name: 'Follow up',
        project_id: null,
        is_dm: true,
        representative_agent_id: 'a1',
        has_updates: false,
      },
    ]

    renderSidebar()

    expect(screen.getByTestId('sidebar-agent-a1')).toBeInTheDocument()
    expect(screen.getByLabelText('읽지 않은 업데이트 있음')).toBeInTheDocument()
  })
})

describe('Sidebar — collapse/expand (#106, hook-backed #115)', () => {
  it('renders the expanded desktop layout when not collapsed', () => {
    renderSidebar()
    const aside = screen.getByTestId('sidebar-root')
    // Default: static, w-64, not translated off at desktop.
    expect(aside.className).toMatch(/md:w-64/)
    expect(aside.className).toMatch(/md:static/)
    expect(aside.className).not.toMatch(/md:w-0/)
    expect(aside).not.toHaveAttribute('aria-hidden')
  })

  it('applies the collapsed desktop classes when hook reports collapsed=true', () => {
    mockSidebarLayout.mockReturnValue({
      collapsed: true,
      toggleCollapsed: toggleCollapsedSpy,
      setCollapsed: vi.fn(),
    })
    renderSidebar()
    const aside = screen.getByTestId('sidebar-root')
    // Collapsed: translated fully off + w-0 so the flex main
    // column reclaims the space. aria-hidden excludes the
    // off-screen subtree from assistive tech.
    expect(aside.className).toMatch(/md:-translate-x-full/)
    expect(aside.className).toMatch(/md:w-0/)
    expect(aside.className).toMatch(/md:overflow-hidden/)
    expect(aside).toHaveAttribute('aria-hidden', 'true')
  })

  it('always renders the desktop collapse trigger and wires it to the hook toggle', () => {
    renderSidebar()
    const trigger = screen.getByTestId('sidebar-collapse')
    expect(trigger).toHaveAttribute('aria-label', 'Collapse sidebar')
    fireEvent.click(trigger)
    expect(toggleCollapsedSpy).toHaveBeenCalledTimes(1)
  })

  it('invokes toggleCollapsed on Ctrl/Cmd+B keydown', () => {
    renderSidebar()
    // Register the keydown on window — matches the useEffect inside
    // Sidebar which installs a global listener while mounted.
    fireEvent.keyDown(window, { key: 'b', ctrlKey: true })
    expect(toggleCollapsedSpy).toHaveBeenCalledTimes(1)

    fireEvent.keyDown(window, { key: 'b', metaKey: true })
    expect(toggleCollapsedSpy).toHaveBeenCalledTimes(2)
  })
})

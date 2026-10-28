// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, screen, fireEvent, cleanup, waitFor, act, within } from '@testing-library/react'
import { requestAgentAction } from '@/lib/agentActions'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter, useLocation } from 'react-router-dom'
import Sidebar from './Sidebar'
import { ThemeProvider } from '@/theme/ThemeProvider'

// Sidebar pulls in a chain of provider-backed hooks. For the
// collapse/expand behaviour (#106/#115) we only care about the root
// ``<aside>`` class & header button, so stub the data hooks out
// with minimal shapes. Each mock mirrors the live shape just
// enough for the component to render without throwing.
const authMockState = vi.hoisted(() => ({
  isAdmin: false,
  logout: vi.fn(),
}))
const updateMockState = vi.hoisted(() => ({ available: false }))
vi.mock('@/hooks/useSystemVersion', () => ({
  useSystemVersion: () => null,
  useUpdateStatus: () => ({
    updates: updateMockState.available ? [{ update_available: true }] : [],
  }),
}))
const roomsMockState = vi.hoisted(() => ({
  projects: [] as Array<{ id: string; name: string; can_delete?: boolean }>,
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
    placement?: {
      machine_id: string
      machine_name: string
      machine_hostname: string
      is_local: boolean
    } | null
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
    availableEngines: [],
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

// MSG-09 — rooms/agents answering right now; the context stays real so
// the Sidebar's own provider wiring is what the tests exercise.
const activeTurnsMockState = vi.hoisted(() => ({
  roomIds: new Set<string>(),
  agentIds: new Set<string>(),
}))
vi.mock('@/hooks/useActiveTurns', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/hooks/useActiveTurns')>(),
  useActiveTurns: () => activeTurnsMockState,
}))

// #115 — Sidebar now reads collapse state from useSidebarLayout.
// Mock the module the same way useRooms/useAuth are mocked; each
// test controls the returned collapsed/toggleCollapsed via the
// ``mockReturnValue`` below.
const toggleCollapsedSpy = vi.fn()
const layoutWidth = { width: 256, setWidth: vi.fn(), resetWidth: vi.fn() }
const mockSidebarLayout = vi.fn(() => ({
  collapsed: false,
  toggleCollapsed: toggleCollapsedSpy,
  setCollapsed: vi.fn(),
  ...layoutWidth,
}))
vi.mock('@/hooks/useSidebarLayout', () => ({
  SIDEBAR_WIDTH: { default: 256, min: 208, max: 480, step: 16 },
  useSidebarLayout: () => mockSidebarLayout(),
}))

// Stub EntityAvatar to avoid pulling in @lobehub/ui transitively
// — the suite is focused on layout/props for the collapse feature,
// avatar rendering is covered by EntityAvatar.test.tsx.
vi.mock('@/components/EntityAvatar', () => ({
  // Surface only the presence wiring (#869); the avatar body is
  // covered by EntityAvatar.test.tsx.
  EntityAvatar: ({ presence }: { presence?: { online: boolean; agentState?: string; working?: boolean } }) =>
    presence ? (
      <span
        data-testid="avatar-presence"
        data-online={String(presence.online)}
        data-state={presence.agentState}
        data-working={String(Boolean(presence.working))}
      />
    ) : null,
}))

beforeEach(() => {
  authMockState.isAdmin = false
  updateMockState.available = false
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
  activeTurnsMockState.roomIds = new Set()
  activeTurnsMockState.agentIds = new Set()
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
    ...layoutWidth,
  })
})

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

function CurrentPath() {
  const location = useLocation()
  return <span data-testid="current-path">{location.pathname}</span>
}

function renderSidebar(path = '/', onClose?: () => void) {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[path]}>
        <Sidebar selectedRoom={null} open={Boolean(onClose)} onClose={onClose} />
        <CurrentPath />
      </MemoryRouter>
    </ThemeProvider>,
  )
}

describe('Sidebar — footer admin menu (#699)', () => {
  it('hides admin navigation from non-admins', () => {
    renderSidebar()
    expect(screen.queryByRole('button', { name: 'Admin settings' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Machines' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Budgets' })).not.toBeInTheDocument()
  })

  it('shows all seven links only after opening the admin settings menu', () => {
    authMockState.isAdmin = true
    renderSidebar()
    const trigger = screen.getByRole('button', { name: 'Admin settings' })
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('button', { name: 'Machines' })).not.toBeInTheDocument()
    fireEvent.click(trigger)
    expect(trigger).toHaveAttribute('aria-expanded', 'true')
    expect(trigger).toHaveAttribute('aria-controls')
    expect(screen.getByRole('group', { name: 'Admin navigation' })).toBeInTheDocument()
    for (const name of ['Machines', 'Skills', 'MCP Servers', 'Usage', 'Budgets']) {
      expect(screen.getByRole('button', { name })).toBeInTheDocument()
    }
    expect(screen.queryByRole('button', { name: 'System' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', {
      name: 'Usage',
    })).toBeInTheDocument()
    // #593 — federation admin entry joins the experimental group while the
    // live wiring is landing (task #33/#34).
    expect(screen.getByRole('button', {
      name: 'Partner nodes, experimental feature',
    })).toBeInTheDocument()
    expect(screen.getByRole('button', {
      name: 'Topology, experimental feature',
    })).toBeInTheDocument()
    expect(screen.getAllByText('Experimental')).toHaveLength(2)
    expect(screen.getByRole('button', { name: 'Machines' })).toHaveFocus()
  })

  it('navigates using the existing sidebar action and closes the mobile drawer', () => {
    authMockState.isAdmin = true
    const onClose = vi.fn()
    renderSidebar('/', onClose)
    fireEvent.click(screen.getByRole('button', { name: 'Admin settings' }))
    fireEvent.click(screen.getByRole('button', { name: 'Skills' }))
    expect(screen.getByTestId('current-path')).toHaveTextContent('/admin/skills')
    expect(onClose).toHaveBeenCalledOnce()
    expect(screen.queryByRole('button', { name: 'Skills' })).not.toBeInTheDocument()
  })

  it('closes on outside pointer down and Escape, restoring focus after Escape', () => {
    authMockState.isAdmin = true
    renderSidebar()
    const trigger = screen.getByRole('button', { name: 'Admin settings' })
    fireEvent.click(trigger)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(trigger).toHaveFocus()
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(trigger)
    fireEvent.pointerDown(document.body)
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
  })

  it('marks the current route and exposes server updates on the Machines link', () => {
    authMockState.isAdmin = true
    updateMockState.available = true
    renderSidebar('/admin/usage/details')
    const trigger = screen.getByRole('button', { name: 'Admin settings, update available' })
    fireEvent.click(trigger)
    expect(screen.getByRole('button', { name: 'Usage' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('button', { name: /Machines/ })).toHaveTextContent('update')
  })
})

describe('Sidebar — add agent entry point', () => {
  it('shows the add-agent action in the agents header for admins, even with no agents yet', () => {
    authMockState.isAdmin = true
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => [] }))
    renderSidebar()
    fireEvent.click(screen.getByRole('button', { name: 'Add agent' }))
    expect(screen.getByRole('dialog')).toHaveTextContent('Create Agent')
  })

  it('hides the add-agent action from non-admins', () => {
    roomsMockState.agentDMs = [{ id: 'dm1', name: 'DM: helper', project_id: null, is_dm: true }]
    renderSidebar()
    expect(screen.getByText('Agents')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Add agent' })).not.toBeInTheDocument()
  })
})

describe('Sidebar — layout (#827)', () => {
  it('places the wordmark before the desktop collapse toggle in the header', () => {
    renderSidebar()
    const home = screen.getByRole('button', { name: 'Go to home' })
    const collapse = screen.getByTestId('sidebar-collapse')
    expect(home.compareDocumentPosition(collapse) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(home).toHaveClass('text-body')
  })

  it('creates projects from the projects section header, not a bottom button', () => {
    renderSidebar()
    expect(screen.getByText('Projects')).toBeInTheDocument()
    // The only creation entry point is the header icon — no labelled bottom button.
    expect(screen.queryByText('New Project')).toBeNull()
    fireEvent.click(screen.getByTestId('sidebar-add-project'))
    expect(screen.getByRole('dialog')).toHaveTextContent('Create Project')
  })
})

describe('Sidebar — projects/agents split (#928)', () => {
  const dm = { id: 'dm1', name: 'DM: helper', project_id: null, is_dm: true }

  it('[ROOM-12] splits the two sections evenly with a resize handle between them', () => {
    roomsMockState.agentDMs = [dm]
    renderSidebar()
    expect(screen.getByTestId('sidebar-sections').style.getPropertyValue('--sidebar-split')).toBe('50%')
    const handle = screen.getByRole('separator', { name: 'Resize projects and agents' })
    expect(handle).toHaveAttribute('aria-controls', 'sidebar-agents-section')
    expect(screen.getByTestId('sidebar-agents-section')).toHaveClass('basis-[var(--sidebar-split)]')
  })

  it('[ROOM-12] restores a stored split', () => {
    roomsMockState.agentDMs = [dm]
    localStorage.setItem('anygarden_sidebar_split_ratio', '0.3')
    renderSidebar()
    expect(screen.getByTestId('sidebar-sections').style.getPropertyValue('--sidebar-split')).toBe('30%')
  })

  it('[ROOM-12] collapsing either section hides the handle and gives the other the space', () => {
    roomsMockState.agentDMs = [dm]
    renderSidebar()
    const agentsToggle = screen.getByRole('button', { name: 'Agents' })

    fireEvent.click(agentsToggle)
    expect(agentsToggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('separator', { name: 'Resize projects and agents' })).toBeNull()
    expect(screen.queryByTestId('sidebar-dm-dm1')).toBeNull()
    expect(screen.getByTestId('sidebar-projects-section')).toHaveClass('flex-1')
    expect(localStorage.getItem('anygarden_sidebar_agents_collapsed')).toBe('true')

    fireEvent.click(agentsToggle)
    const projectsToggle = screen.getByTestId('sidebar-projects-toggle')
    fireEvent.click(projectsToggle)
    expect(projectsToggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('separator', { name: 'Resize projects and agents' })).toBeNull()
    expect(screen.getByTestId('sidebar-agents-section')).toHaveClass('flex-1')
    expect(screen.getByTestId('sidebar-dm-dm1')).toBeInTheDocument()

    fireEvent.click(projectsToggle)
    expect(screen.getByRole('separator', { name: 'Resize projects and agents' })).toBeInTheDocument()
  })

  it('keeps the projects section whole when there is no agents section', () => {
    renderSidebar()
    expect(screen.queryByTestId('sidebar-agents-section')).toBeNull()
    expect(screen.queryByRole('separator', { name: 'Resize projects and agents' })).toBeNull()
    expect(screen.getByTestId('sidebar-projects-section')).toHaveClass('flex-1')
  })
})

describe('Sidebar — agent actions requested from chat', () => {
  beforeEach(() => {
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
      {
        id: 'a2',
        name: 'Agent Two',
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
        has_updates: false,
      },
    ]
  })

  it('opens the existing DM for a direct-message request', () => {
    renderSidebar()
    act(() => requestAgentAction('a1', 'direct-message'))
    expect(screen.getByTestId('current-path')).toHaveTextContent('/rooms/dm1')
    expect(roomsMockState.createAgentDM).not.toHaveBeenCalled()
  })

  it('creates a DM when the agent has none', async () => {
    roomsMockState.createAgentDM.mockResolvedValueOnce({ id: 'dm-new' })
    renderSidebar()
    act(() => requestAgentAction('a2', 'direct-message'))
    await waitFor(() => expect(screen.getByTestId('current-path')).toHaveTextContent('/rooms/dm-new'))
    expect(roomsMockState.createAgentDM).toHaveBeenCalledWith('a2')
  })
})

describe('Sidebar — project delete permission (#783)', () => {
  it('shows the project menu only for projects the caller may delete', () => {
    roomsMockState.projects = [
      { id: 'mine', name: 'Mine', can_delete: true },
      { id: 'shared', name: 'Shared', can_delete: false },
    ]

    renderSidebar()

    expect(screen.getByTestId('sidebar-project-menu-mine')).toBeInTheDocument()
    expect(screen.queryByTestId('sidebar-project-menu-shared')).not.toBeInTheDocument()
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
    expect(screen.getByLabelText('Unread updates')).toBeInTheDocument()
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

    expect(screen.queryByLabelText('Unread updates')).not.toBeInTheDocument()
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
    expect(screen.getByLabelText('Unread updates')).toBeInTheDocument()
  })

  it('[#869] overlays agent presence on the avatar instead of a dot beside the name', () => {
    authMockState.isAdmin = true
    agentsMockState.agents = [
      {
        id: 'a1',
        name: 'agent01',
        engine: 'codex-cli',
        desired_state: 'running',
        actual_state: 'running',
        restart_policy: 'never',
        machine_online: true,
      },
    ]
    roomsMockState.agentDMs = [
      { id: 'dm-a1', name: 'DM: agent01', project_id: null, is_dm: true, representative_agent_id: 'a1' },
    ]

    renderSidebar()

    const row = screen.getByTestId('sidebar-dm-dm-a1')
    const presence = within(row).getByTestId('avatar-presence')
    expect(presence).toHaveAttribute('data-online', 'true')
    expect(presence).toHaveAttribute('data-state', 'running')
    // No standalone PresenceDot between the avatar and the name.
    expect(within(row).queryAllByRole('status')).toHaveLength(0)
  })

  it('[NODE-15] marks only the agent placed on a remote machine', () => {
    authMockState.isAdmin = true
    const base = {
      engine: 'codex-cli',
      desired_state: 'running',
      actual_state: 'running',
      restart_policy: 'never',
      machine_online: true,
    }
    agentsMockState.agents = [
      {
        ...base,
        id: 'local',
        name: 'Host agent',
        placement: { machine_id: 'm1', machine_name: 'Local node', machine_hostname: 'dev', is_local: true },
      },
      {
        ...base,
        id: 'remote',
        name: 'Remote agent',
        placement: { machine_id: 'm2', machine_name: 'anygarden-svc', machine_hostname: 'svc', is_local: false },
      },
    ]
    roomsMockState.agentDMs = [
      { id: 'dm-local', name: 'DM: Host agent', project_id: null, is_dm: true, representative_agent_id: 'local' },
      { id: 'dm-remote', name: 'DM: Remote agent', project_id: null, is_dm: true, representative_agent_id: 'remote' },
    ]

    renderSidebar()

    const badges = screen.getAllByTestId('agent-machine-badge')
    expect(badges).toHaveLength(1)
    expect(badges[0]).toHaveTextContent('anygarden-svc')
    expect(screen.getByTestId('sidebar-dm-dm-remote')).toContainElement(badges[0])
  })
})

describe('Sidebar — responding indicators (#912)', () => {
  const agent = {
    id: 'a1',
    name: 'agent01',
    engine: 'codex-cli',
    desired_state: 'running',
    actual_state: 'running',
    restart_policy: 'never',
    machine_online: true,
  }

  it('[MSG-09] shows responding instead of unread on a project room an agent is answering in', () => {
    localStorage.setItem('anygarden_expanded_projects', JSON.stringify(['p1']))
    roomsMockState.projects = [{ id: 'p1', name: 'Project One' }]
    roomsMockState.rooms = {
      p1: [
        { id: 'r1', name: 'General', project_id: 'p1', is_dm: false, has_updates: true },
        { id: 'r2', name: 'Quiet', project_id: 'p1', is_dm: false, has_updates: true },
      ],
    }
    activeTurnsMockState.roomIds = new Set(['r1'])

    renderSidebar()

    const busy = screen.getByTestId('sidebar-room-r1')
    expect(within(busy).getByRole('status', { name: 'Responding' })).toBeInTheDocument()
    expect(within(busy).queryByLabelText('Unread updates')).not.toBeInTheDocument()
    const quiet = screen.getByTestId('sidebar-room-r2')
    expect(within(quiet).queryByRole('status', { name: 'Responding' })).not.toBeInTheDocument()
    expect(within(quiet).getByLabelText('Unread updates')).toBeInTheDocument()
  })

  it('[MSG-09] rings a working agent and marks its answering DM', () => {
    authMockState.isAdmin = true
    agentsMockState.agents = [agent]
    roomsMockState.agentDMs = [
      { id: 'dm-a1', name: 'DM: agent01', project_id: null, is_dm: true, representative_agent_id: 'a1' },
    ]
    activeTurnsMockState.roomIds = new Set(['dm-a1'])
    activeTurnsMockState.agentIds = new Set(['a1'])

    renderSidebar()

    const row = screen.getByTestId('sidebar-dm-dm-a1')
    expect(within(row).getByTestId('avatar-presence')).toHaveAttribute('data-working', 'true')
    expect(within(row).getByRole('status', { name: 'Responding' })).toBeInTheDocument()
  })

  it('[MSG-09] rings a collapsed multi-DM agent working in a room outside its DMs', () => {
    authMockState.isAdmin = true
    agentsMockState.agents = [agent]
    roomsMockState.agentDMs = [
      { id: 'dm1', name: 'DM: agent01', project_id: null, is_dm: true, representative_agent_id: 'a1' },
      { id: 'dm2', name: 'Follow up', project_id: null, is_dm: true, representative_agent_id: 'a1' },
    ]
    activeTurnsMockState.agentIds = new Set(['a1'])

    renderSidebar()

    const row = screen.getByTestId('sidebar-agent-a1')
    expect(within(row).getByTestId('avatar-presence')).toHaveAttribute('data-working', 'true')
  })

  it('[MSG-09] marks an answering DM for non-admins too', () => {
    roomsMockState.agentDMs = [
      { id: 'dm-x', name: 'DM: helper', project_id: null, is_dm: true, representative_agent_id: 'a9' },
    ]
    activeTurnsMockState.roomIds = new Set(['dm-x'])

    renderSidebar()

    const row = screen.getByTestId('sidebar-dm-dm-x')
    expect(within(row).getByRole('status', { name: 'Responding' })).toBeInTheDocument()
  })
})

describe('Sidebar — collapse/expand (#106, hook-backed #115)', () => {
  it('renders the expanded desktop layout when not collapsed', () => {
    renderSidebar()
    const aside = screen.getByTestId('sidebar-root')
    // Default: in flow at the user's width (#967), not translated off.
    expect(aside.className).toMatch(/md:w-\[var\(--sidebar-w\)\]/)
    expect(aside.style.getPropertyValue('--sidebar-w')).toBe('256px')
    expect(aside.className).toMatch(/md:relative/)
    expect(aside.className).not.toMatch(/md:w-0/)
    expect(aside).not.toHaveAttribute('aria-hidden')
  })

  it('[ROOM-13] offers a width handle on the expanded desktop sidebar only', () => {
    renderSidebar()
    const handle = screen.getByRole('separator', { name: 'Resize sidebar' })
    expect(handle).toHaveAttribute('aria-controls', 'workspace-sidebar')
    expect(handle).toHaveAttribute('aria-valuenow', '256')
    cleanup()

    mockSidebarLayout.mockReturnValue({
      collapsed: true,
      toggleCollapsed: toggleCollapsedSpy,
      setCollapsed: vi.fn(),
      ...layoutWidth,
    })
    renderSidebar()
    expect(screen.queryByRole('separator', { name: 'Resize sidebar' })).toBeNull()
  })

  it('has no width handle in the mobile drawer', () => {
    vi.stubGlobal('innerWidth', 375)
    renderSidebar()
    expect(screen.queryByRole('separator', { name: 'Resize sidebar' })).toBeNull()
  })

  it('applies the collapsed desktop classes when hook reports collapsed=true', () => {
    mockSidebarLayout.mockReturnValue({
      collapsed: true,
      toggleCollapsed: toggleCollapsedSpy,
      setCollapsed: vi.fn(),
      ...layoutWidth,
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
    expect(aside).toHaveAttribute('inert')
  })

  it('keeps a mobile drawer usable after the desktop sidebar was collapsed', () => {
    vi.stubGlobal('innerWidth', 375)
    mockSidebarLayout.mockReturnValue({
      collapsed: true,
      toggleCollapsed: toggleCollapsedSpy,
      setCollapsed: vi.fn(),
      ...layoutWidth,
    })
    const onClose = vi.fn()
    renderSidebar('/', onClose)
    const aside = screen.getByTestId('sidebar-root')
    expect(aside).not.toHaveAttribute('aria-hidden')
    expect(aside).not.toHaveAttribute('inert')
    fireEvent.click(screen.getByRole('button', { name: 'Personal settings' }))
    expect(screen.getByRole('group', { name: 'Language' })).toBeInTheDocument()
  })

  it('always renders the desktop collapse trigger and wires it to the hook toggle', () => {
    renderSidebar()
    const trigger = screen.getByTestId('sidebar-collapse')
    expect(trigger).toHaveAttribute('aria-label', 'Collapse sidebar')
    expect(trigger).toHaveAttribute('aria-controls', 'workspace-sidebar')
    expect(trigger).toHaveAttribute('aria-expanded', 'true')
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

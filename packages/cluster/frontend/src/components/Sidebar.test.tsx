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

vi.mock('@/hooks/useAuth', () => ({
  useAuth: () => ({
    user: { id: 'u1', email: 'u@example.com', is_admin: authMockState.isAdmin },
    logout: authMockState.logout,
  }),
}))
vi.mock('@/hooks/useAgents', () => ({
  useAgents: () => ({ agents: [] }),
}))
vi.mock('@/hooks/useRooms', () => ({
  useRooms: () => ({
    projects: [],
    rooms: {},
    agentDMs: [],
    createProject: vi.fn(),
    deleteProject: vi.fn(),
    createRoom: vi.fn(),
    fetchRooms: vi.fn(),
    pinRoom: vi.fn(),
    reorderPinnedRooms: vi.fn(),
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
  it('marks LLM Gateway and Topology as experimental beta entries', () => {
    authMockState.isAdmin = true

    renderSidebar()

    expect(screen.getByRole('button', {
      name: 'LLM Gateway, experimental feature',
    })).toBeInTheDocument()
    expect(screen.getByRole('button', {
      name: 'Topology, experimental feature',
    })).toBeInTheDocument()
    expect(screen.getAllByText('Beta')).toHaveLength(2)
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

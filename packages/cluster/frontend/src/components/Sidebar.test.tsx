// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter } from 'react-router-dom'
import Sidebar from './Sidebar'

// Sidebar pulls in a chain of provider-backed hooks. For the
// collapse/expand behaviour (#106) we only care about the root
// ``<aside>`` class & header button, so stub the data hooks out
// with minimal shapes. Each mock mirrors the live shape just
// enough for the component to render without throwing.
vi.mock('@/hooks/useAuth', () => ({
  useAuth: () => ({
    user: { id: 'u1', email: 'u@example.com', is_admin: false },
    logout: vi.fn(),
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

// Stub EntityAvatar to avoid pulling in @lobehub/ui transitively
// — the suite is focused on layout/props for the collapse feature,
// avatar rendering is covered by EntityAvatar.test.tsx.
vi.mock('@/components/EntityAvatar', () => ({
  EntityAvatar: () => null,
}))

afterEach(() => cleanup())

function renderSidebar(
  props: Partial<React.ComponentProps<typeof Sidebar>> = {},
) {
  return render(
    <MemoryRouter>
      <Sidebar selectedRoom={null} {...props} />
    </MemoryRouter>,
  )
}

describe('Sidebar — collapse/expand (#106)', () => {
  it('renders the expanded desktop layout when not collapsed', () => {
    renderSidebar({ collapsed: false })
    const aside = screen.getByTestId('sidebar-root')
    // Default: static, w-64, not translated off at desktop.
    expect(aside.className).toMatch(/md:w-64/)
    expect(aside.className).toMatch(/md:static/)
    expect(aside.className).not.toMatch(/md:w-0/)
    expect(aside).not.toHaveAttribute('aria-hidden')
  })

  it('applies the collapsed desktop classes when collapsed=true', () => {
    renderSidebar({ collapsed: true, onToggleCollapsed: vi.fn() })
    const aside = screen.getByTestId('sidebar-root')
    // Collapsed: translated fully off + w-0 so the flex main
    // column reclaims the space. aria-hidden excludes the
    // off-screen subtree from assistive tech.
    expect(aside.className).toMatch(/md:-translate-x-full/)
    expect(aside.className).toMatch(/md:w-0/)
    expect(aside.className).toMatch(/md:overflow-hidden/)
    expect(aside).toHaveAttribute('aria-hidden', 'true')
  })

  it('renders the desktop collapse trigger when onToggleCollapsed is provided', () => {
    const onToggleCollapsed = vi.fn()
    renderSidebar({ onToggleCollapsed })
    const trigger = screen.getByTestId('sidebar-collapse')
    expect(trigger).toHaveAttribute('aria-label', 'Collapse sidebar')
    fireEvent.click(trigger)
    expect(onToggleCollapsed).toHaveBeenCalledTimes(1)
  })

  it('omits the collapse trigger when no handler is supplied', () => {
    renderSidebar()
    expect(screen.queryByTestId('sidebar-collapse')).toBeNull()
  })
})

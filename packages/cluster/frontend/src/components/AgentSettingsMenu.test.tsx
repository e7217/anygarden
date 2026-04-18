// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import AgentSettingsMenu from './AgentSettingsMenu'

afterEach(() => cleanup())

describe('AgentSettingsMenu', () => {
  it('renders nothing when no handlers are supplied', () => {
    const { container } = render(<AgentSettingsMenu />)
    expect(container.firstChild).toBeNull()
  })

  it('renders only items whose handler is supplied', () => {
    render(
      <AgentSettingsMenu
        onEditAvatar={vi.fn()}
        onEditManifest={vi.fn()}
      />,
    )
    fireEvent.click(screen.getByTestId('agent-settings-menu-trigger'))
    expect(screen.getByText('Edit avatar')).toBeInTheDocument()
    expect(screen.getByText('Edit manifest')).toBeInTheDocument()
    expect(screen.queryByText('Manage rooms')).toBeNull()
    expect(screen.queryByText('Activity')).toBeNull()
    expect(screen.queryByText('Copy agent ID')).toBeNull()
    expect(screen.queryByText('Delete agent')).toBeNull()
  })

  it('invokes the matching callback and closes the menu on selection', () => {
    const onEditAvatar = vi.fn()
    render(<AgentSettingsMenu onEditAvatar={onEditAvatar} />)
    fireEvent.click(screen.getByTestId('agent-settings-menu-trigger'))
    fireEvent.click(screen.getByTestId('agent-menu-edit-avatar'))
    expect(onEditAvatar).toHaveBeenCalledTimes(1)
    // Menu closes after selection — the item should no longer be in the DOM.
    expect(screen.queryByTestId('agent-menu-edit-avatar')).toBeNull()
  })

  it('separates Delete behind a divider and styles it as destructive', () => {
    render(
      <AgentSettingsMenu
        onEditAvatar={vi.fn()}
        onDelete={vi.fn()}
      />,
    )
    fireEvent.click(screen.getByTestId('agent-settings-menu-trigger'))
    const del = screen.getByTestId('agent-menu-delete')
    expect(del.className).toMatch(/text-red-600/)
  })

  it('closes on Escape', () => {
    render(<AgentSettingsMenu onEditAvatar={vi.fn()} />)
    fireEvent.click(screen.getByTestId('agent-settings-menu-trigger'))
    expect(screen.getByTestId('agent-menu-edit-avatar')).toBeInTheDocument()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByTestId('agent-menu-edit-avatar')).toBeNull()
  })

  it('closes on outside pointer click', () => {
    render(
      <div>
        <div data-testid="outside">outside</div>
        <AgentSettingsMenu onEditAvatar={vi.fn()} />
      </div>,
    )
    fireEvent.click(screen.getByTestId('agent-settings-menu-trigger'))
    expect(screen.getByTestId('agent-menu-edit-avatar')).toBeInTheDocument()
    fireEvent.pointerDown(screen.getByTestId('outside'))
    expect(screen.queryByTestId('agent-menu-edit-avatar')).toBeNull()
  })
})

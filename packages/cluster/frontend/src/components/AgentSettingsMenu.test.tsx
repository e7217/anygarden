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

  it('renders Settings… only when onOpenSettings is supplied', () => {
    render(<AgentSettingsMenu onOpenSettings={vi.fn()} />)
    fireEvent.click(screen.getByTestId('agent-settings-menu-trigger'))
    expect(screen.getByText('Settings…')).toBeInTheDocument()
    expect(screen.queryByText('Delete agent')).toBeNull()
    expect(screen.queryByText('대화 맥락 공유 제외')).toBeNull()
  })

  it('invokes onOpenSettings and closes the menu on selection', () => {
    const onOpenSettings = vi.fn()
    render(<AgentSettingsMenu onOpenSettings={onOpenSettings} />)
    fireEvent.click(screen.getByTestId('agent-settings-menu-trigger'))
    fireEvent.click(screen.getByTestId('agent-menu-settings'))
    expect(onOpenSettings).toHaveBeenCalledTimes(1)
    expect(screen.queryByTestId('agent-menu-settings')).toBeNull()
  })

  it('separates Delete behind a divider and styles it as destructive', () => {
    render(
      <AgentSettingsMenu
        onOpenSettings={vi.fn()}
        onDelete={vi.fn()}
      />,
    )
    fireEvent.click(screen.getByTestId('agent-settings-menu-trigger'))
    const del = screen.getByTestId('agent-menu-delete')
    expect(del.className).toMatch(/text-red-600/)
  })

  it('closes on Escape', () => {
    render(<AgentSettingsMenu onOpenSettings={vi.fn()} />)
    fireEvent.click(screen.getByTestId('agent-settings-menu-trigger'))
    expect(screen.getByTestId('agent-menu-settings')).toBeInTheDocument()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByTestId('agent-menu-settings')).toBeNull()
  })

  it('closes on outside pointer click', () => {
    render(
      <div>
        <div data-testid="outside">outside</div>
        <AgentSettingsMenu onOpenSettings={vi.fn()} />
      </div>,
    )
    fireEvent.click(screen.getByTestId('agent-settings-menu-trigger'))
    expect(screen.getByTestId('agent-menu-settings')).toBeInTheDocument()
    fireEvent.pointerDown(screen.getByTestId('outside'))
    expect(screen.queryByTestId('agent-menu-settings')).toBeNull()
  })

  // #148 Part 2 — context-window opt-out toggle.
  describe('context window opt-out toggle', () => {
    it('renders the toggle only when both props are provided', () => {
      render(
        <AgentSettingsMenu
          onOpenSettings={vi.fn()}
          contextWindowOptOut={false}
          onToggleContextWindowOptOut={vi.fn()}
        />,
      )
      fireEvent.click(screen.getByTestId('agent-settings-menu-trigger'))
      expect(
        screen.getByTestId('agent-menu-context-window-opt-out'),
      ).toBeInTheDocument()
    })

    it('omits the toggle when only one of the pair is supplied', () => {
      render(
        <AgentSettingsMenu
          onOpenSettings={vi.fn()}
          // contextWindowOptOut omitted → toggle must not render even
          // though the handler is present.
          onToggleContextWindowOptOut={vi.fn()}
        />,
      )
      fireEvent.click(screen.getByTestId('agent-settings-menu-trigger'))
      expect(
        screen.queryByTestId('agent-menu-context-window-opt-out'),
      ).toBeNull()
    })

    it('renders aria-checked=false when the flag is off', () => {
      render(
        <AgentSettingsMenu
          contextWindowOptOut={false}
          onToggleContextWindowOptOut={vi.fn()}
        />,
      )
      fireEvent.click(screen.getByTestId('agent-settings-menu-trigger'))
      expect(
        screen.getByTestId('agent-menu-context-window-opt-out'),
      ).toHaveAttribute('aria-checked', 'false')
    })

    it('renders aria-checked=true when the flag is on', () => {
      render(
        <AgentSettingsMenu
          contextWindowOptOut={true}
          onToggleContextWindowOptOut={vi.fn()}
        />,
      )
      fireEvent.click(screen.getByTestId('agent-settings-menu-trigger'))
      expect(
        screen.getByTestId('agent-menu-context-window-opt-out'),
      ).toHaveAttribute('aria-checked', 'true')
    })

    it('invokes the toggle handler and closes the menu', () => {
      const onToggle = vi.fn()
      render(
        <AgentSettingsMenu
          contextWindowOptOut={false}
          onToggleContextWindowOptOut={onToggle}
        />,
      )
      fireEvent.click(screen.getByTestId('agent-settings-menu-trigger'))
      fireEvent.click(
        screen.getByTestId('agent-menu-context-window-opt-out'),
      )
      expect(onToggle).toHaveBeenCalledTimes(1)
      expect(
        screen.queryByTestId('agent-menu-context-window-opt-out'),
      ).toBeNull()
    })
  })

  // #241 — compact variant for sidebar agent rows.
  describe('compact variant', () => {
    it('renders a 24×24 bare trigger when compact is true', () => {
      render(<AgentSettingsMenu compact onOpenSettings={vi.fn()} />)
      const trigger = screen.getByTestId('agent-settings-menu-trigger')
      // Bare <button> element, not the shadcn Button wrapper.
      expect(trigger.tagName).toBe('BUTTON')
      // h-6 w-6 geometry matches the sibling ``+`` (new DM) button
      // so the two align vertically in the sidebar row.
      expect(trigger.className).toMatch(/\bh-6\b/)
      expect(trigger.className).toMatch(/\bw-6\b/)
    })

    it('defaults to the shadcn Button (h-9) when compact is omitted', () => {
      render(<AgentSettingsMenu onOpenSettings={vi.fn()} />)
      const trigger = screen.getByTestId('agent-settings-menu-trigger')
      // shadcn Button size="icon" yields ``size-9`` — no compact
      // h-6 leak into the default path.
      expect(trigger.className).not.toMatch(/\bh-6\b/)
    })

    it('still opens the menu on click in compact mode', () => {
      render(<AgentSettingsMenu compact onOpenSettings={vi.fn()} />)
      fireEvent.click(screen.getByTestId('agent-settings-menu-trigger'))
      expect(screen.getByTestId('agent-menu-settings')).toBeInTheDocument()
    })
  })
})

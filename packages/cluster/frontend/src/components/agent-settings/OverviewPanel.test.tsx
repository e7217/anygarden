// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, screen, fireEvent, cleanup, waitFor, act } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'

// EntityAvatar's engine glyph pulls in @lobehub/icons which can
// confuse vitest's ESM loader — stub it as in the AvatarPickerPanel
// suite.
vi.mock('@/components/EngineGlyph', () => ({
  EngineGlyph: ({ engine }: { engine: string | undefined }) => (
    <svg data-testid={`engine-${engine ?? 'none'}`} />
  ),
}))

import OverviewPanel from './OverviewPanel'
import type { Agent } from '@/hooks/useAgents'

afterEach(() => cleanup())

function makeAgent(overrides: Partial<Agent> = {}): Agent {
  return {
    id: 'agent_abc123',
    name: 'greeting-bot',
    engine: 'claude-code',
    desired_state: 'running',
    actual_state: 'online',
    restart_policy: 'always',
    agents_md: null,
    avatar_kind: null,
    avatar_value: null,
    ...overrides,
  }
}

function setup(agent: Agent | null = makeAgent()) {
  const updateAgent = vi.fn().mockResolvedValue(makeAgent())
  render(<OverviewPanel agent={agent} updateAgent={updateAgent} />)
  return { updateAgent }
}

describe('OverviewPanel', () => {
  it('renders empty state when no agent is selected', () => {
    setup(null)
    expect(screen.getByTestId('overview-panel-empty')).toBeInTheDocument()
  })

  it('renders the agent id, engine, and state', () => {
    setup()
    expect(screen.getByTestId('overview-id-text')).toHaveTextContent('agent_abc123')
    expect(screen.getByText('claude-code')).toBeInTheDocument()
    expect(screen.getByText('online')).toBeInTheDocument()
  })

  describe('name inline edit', () => {
    it('commits on blur when the name changed', async () => {
      const { updateAgent } = setup()
      const input = screen.getByTestId('overview-name-input') as HTMLInputElement
      fireEvent.change(input, { target: { value: 'renamed-bot' } })
      fireEvent.blur(input)

      await waitFor(() => expect(updateAgent).toHaveBeenCalledTimes(1))
      expect(updateAgent).toHaveBeenCalledWith('agent_abc123', { name: 'renamed-bot' })
    })

    it('does nothing when the name was not changed', () => {
      const { updateAgent } = setup()
      const input = screen.getByTestId('overview-name-input') as HTMLInputElement
      fireEvent.blur(input)
      expect(updateAgent).not.toHaveBeenCalled()
    })

    it('rolls back the input on save failure', async () => {
      const updateAgent = vi.fn().mockRejectedValue(new Error('boom'))
      render(<OverviewPanel agent={makeAgent()} updateAgent={updateAgent} />)
      const input = screen.getByTestId('overview-name-input') as HTMLInputElement
      fireEvent.change(input, { target: { value: 'renamed-bot' } })
      fireEvent.blur(input)

      await waitFor(() =>
        expect(screen.getByTestId('overview-name-error')).toBeInTheDocument(),
      )
      expect(input.value).toBe('greeting-bot')
    })

    it('reverts draft and blurs on Escape', () => {
      const { updateAgent } = setup()
      const input = screen.getByTestId('overview-name-input') as HTMLInputElement
      fireEvent.change(input, { target: { value: 'renamed-bot' } })
      fireEvent.keyDown(input, { key: 'Escape' })
      expect(input.value).toBe('greeting-bot')
      expect(updateAgent).not.toHaveBeenCalled()
    })
  })

  describe('avatar picker', () => {
    it('toggles the inline AvatarPickerPanel on avatar click', async () => {
      setup()
      expect(screen.queryByTestId('avatar-picker-save')).toBeNull()
      fireEvent.click(screen.getByTestId('overview-avatar-trigger'))
      expect(await screen.findByTestId('avatar-picker-save')).toBeInTheDocument()
    })
  })

  describe('copy id', () => {
    // navigator.clipboard is not provided by jsdom — install a stub.
    let writeTextSpy: ReturnType<typeof vi.fn>

    beforeEach(() => {
      writeTextSpy = vi.fn().mockResolvedValue(undefined)
      Object.defineProperty(navigator, 'clipboard', {
        value: { writeText: writeTextSpy },
        configurable: true,
      })
    })

    it('writes the agent id to the clipboard and shows Copied', async () => {
      setup()
      fireEvent.click(screen.getByTestId('overview-copy-id'))
      await waitFor(() => expect(writeTextSpy).toHaveBeenCalledWith('agent_abc123'))
      expect(await screen.findByTestId('overview-copy-feedback')).toHaveTextContent(
        'Copied',
      )
    })

    it('falls back to "Clipboard unavailable" when writeText rejects', async () => {
      writeTextSpy.mockRejectedValue(new Error('denied'))
      setup()
      await act(async () => {
        fireEvent.click(screen.getByTestId('overview-copy-id'))
      })
      expect(await screen.findByTestId('overview-copy-feedback')).toHaveTextContent(
        /Clipboard unavailable/i,
      )
    })
  })
})

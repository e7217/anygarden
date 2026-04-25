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
import type { Agent, EngineCatalog } from '@/hooks/useAgents'

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

// Matches the shape the catalog endpoint returns for ``claude-code``
// so the Overview dropdowns have something realistic to render.
function makeClaudeCatalog(): EngineCatalog {
  return {
    engine: 'claude-code',
    default_model: 'claude-opus-4-7',
    models: [
      { id: 'claude-opus-4-7', label: 'Claude Opus 4.7', reasoning_levels: [] },
      { id: 'claude-sonnet-4-6', label: 'Claude Sonnet 4.6', reasoning_levels: [] },
      { id: 'claude-haiku-4-5', label: 'Claude Haiku 4.5', reasoning_levels: [] },
    ],
    reasoning_levels: ['low', 'medium', 'high', 'xhigh', 'max'],
  }
}

interface SetupOpts {
  agent?: Agent | null
  catalog?: EngineCatalog | null
  catalogFetcher?: (engine: string) => Promise<EngineCatalog | null>
  updateAgent?: ReturnType<typeof vi.fn>
}

function setup(opts: SetupOpts = {}) {
  const agent = opts.agent === undefined ? makeAgent() : opts.agent
  const updateAgent =
    opts.updateAgent ?? vi.fn().mockResolvedValue(makeAgent())
  const catalogFetcher =
    opts.catalogFetcher ??
    vi
      .fn<(engine: string) => Promise<EngineCatalog | null>>()
      .mockResolvedValue(opts.catalog === undefined ? makeClaudeCatalog() : opts.catalog)
  render(
    <OverviewPanel
      agent={agent}
      updateAgent={updateAgent}
      fetchEngineCatalog={catalogFetcher}
    />,
  )
  return { updateAgent, catalogFetcher }
}

describe('OverviewPanel', () => {
  it('renders empty state when no agent is selected', () => {
    setup({ agent: null })
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

  describe('description inline edit (#271)', () => {
    it('commits with description_set: true on blur after typing', async () => {
      const { updateAgent } = setup()
      const input = screen.getByTestId(
        'overview-description-input',
      ) as HTMLTextAreaElement
      fireEvent.change(input, { target: { value: 'Reviews React UIs' } })
      fireEvent.blur(input)

      await waitFor(() => expect(updateAgent).toHaveBeenCalledTimes(1))
      expect(updateAgent).toHaveBeenCalledWith('agent_abc123', {
        description: 'Reviews React UIs',
        description_set: true,
      })
    })

    it('clears the description by sending null when the field is emptied', async () => {
      const { updateAgent } = setup({
        agent: makeAgent({ description: 'old intro' }),
      })
      const input = screen.getByTestId(
        'overview-description-input',
      ) as HTMLTextAreaElement
      fireEvent.change(input, { target: { value: '' } })
      fireEvent.blur(input)

      await waitFor(() => expect(updateAgent).toHaveBeenCalledTimes(1))
      expect(updateAgent).toHaveBeenCalledWith('agent_abc123', {
        description: null,
        description_set: true,
      })
    })

    it('does not call updateAgent when the description is unchanged', () => {
      const { updateAgent } = setup({
        agent: makeAgent({ description: 'unchanged' }),
      })
      const input = screen.getByTestId(
        'overview-description-input',
      ) as HTMLTextAreaElement
      fireEvent.blur(input)
      expect(updateAgent).not.toHaveBeenCalled()
    })

    it('updates the live counter as the admin types', () => {
      setup()
      const input = screen.getByTestId(
        'overview-description-input',
      ) as HTMLTextAreaElement
      fireEvent.change(input, { target: { value: 'hello' } })
      expect(screen.getByTestId('overview-description-counter')).toHaveTextContent(
        '5/200',
      )
    })

    it('reverts to the stored value on Escape', () => {
      const { updateAgent } = setup({
        agent: makeAgent({ description: 'stored intro' }),
      })
      const input = screen.getByTestId(
        'overview-description-input',
      ) as HTMLTextAreaElement
      fireEvent.change(input, { target: { value: 'draft change' } })
      fireEvent.keyDown(input, { key: 'Escape' })
      expect(input.value).toBe('stored intro')
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

  describe('model / reasoning dropdowns', () => {
    it('populates options from the fetched catalog and preselects the current model', async () => {
      setup({ agent: makeAgent({ model: 'claude-sonnet-4-6' }) })
      const select = (await screen.findByTestId(
        'overview-model-select',
      )) as HTMLSelectElement
      expect(select.value).toBe('claude-sonnet-4-6')
      // default + 3 catalog entries
      expect(select.querySelectorAll('option').length).toBe(4)
    })

    it('saves with model_set: true when the admin picks a new model', async () => {
      const { updateAgent } = setup({ agent: makeAgent({ model: 'claude-opus-4-7' }) })
      const select = (await screen.findByTestId(
        'overview-model-select',
      )) as HTMLSelectElement
      fireEvent.change(select, { target: { value: 'claude-sonnet-4-6' } })
      await waitFor(() => expect(updateAgent).toHaveBeenCalledTimes(1))
      expect(updateAgent).toHaveBeenCalledWith('agent_abc123', {
        model: 'claude-sonnet-4-6',
        model_set: true,
      })
    })

    it('clears the model to null when the admin picks Default', async () => {
      const { updateAgent } = setup({ agent: makeAgent({ model: 'claude-opus-4-7' }) })
      const select = (await screen.findByTestId(
        'overview-model-select',
      )) as HTMLSelectElement
      fireEvent.change(select, { target: { value: '' } })
      await waitFor(() => expect(updateAgent).toHaveBeenCalledTimes(1))
      expect(updateAgent).toHaveBeenCalledWith('agent_abc123', {
        model: null,
        model_set: true,
      })
    })

    it('saves reasoning_effort with reasoning_effort_set: true', async () => {
      const { updateAgent } = setup({
        agent: makeAgent({ reasoning_effort: 'low' }),
      })
      const select = (await screen.findByTestId(
        'overview-reasoning-select',
      )) as HTMLSelectElement
      fireEvent.change(select, { target: { value: 'high' } })
      await waitFor(() => expect(updateAgent).toHaveBeenCalledTimes(1))
      expect(updateAgent).toHaveBeenCalledWith('agent_abc123', {
        reasoning_effort: 'high',
        reasoning_effort_set: true,
      })
    })

    it('preserves a legacy model value with a disabled option', async () => {
      setup({ agent: makeAgent({ model: 'claude-opus-4-6-fast' }) })
      const select = (await screen.findByTestId(
        'overview-model-select',
      )) as HTMLSelectElement
      expect(select.value).toBe('claude-opus-4-6-fast')
      const legacy = screen.getByText(
        /Current: claude-opus-4-6-fast \(no longer in catalog\)/,
      )
      expect(legacy.tagName).toBe('OPTION')
      expect((legacy as HTMLOptionElement).disabled).toBe(true)
    })

    it('hides the dropdown rows when the catalog is unavailable', async () => {
      setup({ catalog: null })
      // Wait a microtask for the catalog promise to resolve, then confirm.
      await waitFor(() =>
        expect(screen.queryByTestId('overview-model-select')).toBeNull(),
      )
      expect(screen.queryByTestId('overview-reasoning-select')).toBeNull()
    })

    it('shows an inline error when updateAgent rejects', async () => {
      const updateAgent = vi.fn().mockRejectedValue(new Error('PUT failed'))
      setup({ agent: makeAgent({ model: 'claude-opus-4-7' }), updateAgent })
      const select = (await screen.findByTestId(
        'overview-model-select',
      )) as HTMLSelectElement
      fireEvent.change(select, { target: { value: 'claude-sonnet-4-6' } })
      await waitFor(() =>
        expect(screen.getByTestId('overview-config-error')).toHaveTextContent(
          'PUT failed',
        ),
      )
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

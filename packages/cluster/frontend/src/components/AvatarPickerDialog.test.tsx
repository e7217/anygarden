// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'

// The preview in this dialog mounts EntityAvatar, which pulls in
// @lobehub/icons via EngineGlyph. Some @lobehub subpaths don't
// resolve correctly under vitest's ESM loader, so stub the glyph
// with a trivial inline component — the picker's behavior doesn't
// depend on which engine glyph actually renders.
vi.mock('@/components/EngineGlyph', () => ({
  EngineGlyph: ({ engine }: { engine: string | undefined }) => (
    <svg data-testid={`engine-${engine ?? 'none'}`} />
  ),
}))

import AvatarPickerDialog from './AvatarPickerDialog'
import type { Agent } from '@/hooks/useAgents'

afterEach(() => cleanup())

function makeAgent(overrides: Partial<Agent> = {}): Agent {
  return {
    id: 'a1',
    name: 'bot',
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
  const updateAgent = vi
    .fn()
    .mockResolvedValue(makeAgent({ avatar_kind: 'emoji', avatar_value: '🤖' }))
  const onOpenChange = vi.fn()
  render(
    <AvatarPickerDialog
      agent={agent}
      open={true}
      onOpenChange={onOpenChange}
      updateAgent={updateAgent}
    />,
  )
  return { updateAgent, onOpenChange }
}

describe('AvatarPickerDialog', () => {
  it('emoji click stages the emoji and enables Save', async () => {
    const { updateAgent } = setup()
    fireEvent.click(await screen.findByTestId('avatar-picker-emoji-🤖'))
    const save = screen.getByTestId('avatar-picker-save')
    expect(save).not.toBeDisabled()

    fireEvent.click(save)
    await waitFor(() => expect(updateAgent).toHaveBeenCalledTimes(1))
    expect(updateAgent).toHaveBeenCalledWith('a1', {
      avatar_kind_set: true,
      avatar_kind: 'emoji',
      avatar_value_set: true,
      avatar_value: '🤖',
    })
  })

  it('icon tab click stages a lucide name', async () => {
    const { updateAgent } = setup()
    // Radix Tabs activate on pointerDown, not click — ``fireEvent.click``
    // alone leaves the default tab selected in jsdom.
    const lucideTab = screen.getByTestId('avatar-picker-tab-lucide')
    // Radix's TabsTrigger activates on mouseDown with button===0,
    // not click — fireEvent.click alone leaves the default tab in jsdom.
    fireEvent.mouseDown(lucideTab, { button: 0 })
    fireEvent.click(await screen.findByTestId('avatar-picker-lucide-Rocket'))
    fireEvent.click(screen.getByTestId('avatar-picker-save'))

    await waitFor(() => expect(updateAgent).toHaveBeenCalledTimes(1))
    expect(updateAgent).toHaveBeenCalledWith('a1', {
      avatar_kind_set: true,
      avatar_kind: 'lucide',
      avatar_value_set: true,
      avatar_value: 'Rocket',
    })
  })

  it('Reset tab stages null / null so Save clears the override', async () => {
    const { updateAgent } = setup(
      makeAgent({ avatar_kind: 'emoji', avatar_value: '🤖' }),
    )
    const resetTab = screen.getByTestId('avatar-picker-tab-reset')
    // Radix's TabsTrigger activates on mouseDown with button===0.
    fireEvent.mouseDown(resetTab, { button: 0 })
    fireEvent.click(await screen.findByTestId('avatar-picker-reset'))
    fireEvent.click(screen.getByTestId('avatar-picker-save'))

    await waitFor(() => expect(updateAgent).toHaveBeenCalledTimes(1))
    expect(updateAgent).toHaveBeenCalledWith('a1', {
      avatar_kind_set: true,
      avatar_kind: null,
      avatar_value_set: true,
      avatar_value: null,
    })
  })

  it('Save is disabled when nothing changed from the initial state', async () => {
    setup(makeAgent({ avatar_kind: 'emoji', avatar_value: '🤖' }))
    // Nothing clicked → draft still equals initial → Save disabled.
    expect(screen.getByTestId('avatar-picker-save')).toBeDisabled()
  })
})

// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup, screen, within } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'

// Keep @lobehub/icons out of the unit-test bundle.
vi.mock('@/components/EngineGlyph', () => ({ EngineGlyph: () => null }))

import ParticipantListPopover from './ParticipantListPopover'

afterEach(() => cleanup())

describe('ParticipantListPopover — presence (#869)', () => {
  it('overlays each participant presence on its avatar', () => {
    render(
      <ParticipantListPopover
        open
        onClose={() => {}}
        participants={{
          a: { id: 'a', display_name: 'agent01', kind: 'agent', online: true },
          u: { id: 'u', display_name: 'Alice', kind: 'user', online: false },
        }}
        presence={{ u: { online: true, lastSeenAt: null } }}
      />,
    )

    const group = screen.getByRole('group')
    const dots = within(group).getAllByRole('status')
    expect(dots).toHaveLength(2)
    for (const dot of dots) {
      expect(dot).toHaveAttribute('data-testid', 'entity-avatar-presence')
    }
    // Live presence patches win over the participant snapshot.
    expect(dots.map(d => d.dataset.presence)).toEqual(['online', 'online'])
  })
})

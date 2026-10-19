// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup, screen, within, fireEvent } from '@testing-library/react'
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

describe('ParticipantListPopover — representative agent (#897)', () => {
  const participants = {
    p1: { id: 'p1', agent_id: 'a1', display_name: 'PM', kind: 'agent', online: true },
    p2: { id: 'p2', agent_id: 'a2', display_name: 'Dev', kind: 'agent', online: false },
    u: { id: 'u', display_name: 'Alice', kind: 'user', online: true },
  }

  it('[ROOM-10] badges the representative agent for every viewer', () => {
    render(
      <ParticipantListPopover open onClose={() => {}} participants={participants} representativeAgentId="a1" />,
    )
    expect(screen.getByTestId('participant-representative-p1')).toHaveTextContent('Representative')
    expect(screen.queryByTestId('participant-representative-p2')).toBeNull()
    // Without a handler the viewer can see the representative but not change it.
    expect(screen.queryByRole('combobox')).toBeNull()
  })

  it('[ROOM-10] lets an admin set and clear the representative from the list', () => {
    const onSetRepresentative = vi.fn()
    render(
      <ParticipantListPopover
        open
        onClose={() => {}}
        participants={participants}
        representativeAgentId="a1"
        onSetRepresentative={onSetRepresentative}
      />,
    )
    const select = screen.getByRole('combobox', { name: 'Representative agent' })
    expect(select).toHaveValue('a1')
    expect(within(select).getByRole('option', { name: 'Dev (offline)' })).toBeInTheDocument()
    expect(within(select).queryByRole('option', { name: 'Alice' })).toBeNull()
    fireEvent.change(select, { target: { value: 'a2' } })
    expect(onSetRepresentative).toHaveBeenLastCalledWith('a2')
    fireEvent.change(select, { target: { value: '' } })
    expect(onSetRepresentative).toHaveBeenLastCalledWith(null)
  })

  it('omits the picker when the room has no agents', () => {
    render(
      <ParticipantListPopover
        open
        onClose={() => {}}
        participants={{ u: participants.u }}
        onSetRepresentative={() => {}}
      />,
    )
    expect(screen.queryByRole('combobox')).toBeNull()
  })
})

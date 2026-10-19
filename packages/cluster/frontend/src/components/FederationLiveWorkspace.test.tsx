// @vitest-environment jsdom
// UI tests for the live federation workspace (#593 / task #33). Verifies the
// honest-state surfaces: disabled banner when the node has no federation
// wiring and roster with tombstone revisions. Task handoff lives in the
// shared room itself (#889).
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { cleanup, render as renderView, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import '@testing-library/jest-dom/vitest'
import FederationLiveWorkspace from './FederationLiveWorkspace'
import { FederationApiError, type ParticipantView } from '@/lib/federationApi'
import type { useFederation } from '@/hooks/useFederation'
import type { Room } from '@/hooks/useRooms'

const roomsState = vi.hoisted(() => ({ rooms: {} as Record<string, Room[]>, projects: [] as { id: string; name: string }[] }))
vi.mock('@/hooks/useRooms', () => ({
  useRooms: () => ({ projects: roomsState.projects, rooms: roomsState.rooms, refetch: vi.fn().mockResolvedValue(undefined) }),
}))

type Federation = ReturnType<typeof useFederation>

const render = (ui: Parameters<typeof renderView>[0]) => renderView(<MemoryRouter>{ui}</MemoryRouter>)

function baseFederation(overrides: Partial<Federation> = {}): Federation {
  return {
    isAdmin: true,
    invites: [],
    peers: [],
    nodesError: null,
    nodesCapability: 'ready',
    refreshNodes: vi.fn(),
    channelRef: null,
    setChannelRef: vi.fn(),
    bindings: [],
    snapshot: null,
    snapshotError: null,
    roster: [],
    delegations: [],
    submissions: [],
    actorPrincipal: null,
    busy: false,
    actionError: null,
    createInvite: vi.fn(),
    acceptInvite: vi.fn(),
    revokeInvite: vi.fn(),
    revokePeer: vi.fn(),
    revokeGrant: vi.fn(),
    bindChannel: vi.fn(),
    setPublication: vi.fn(),
    changeParticipant: vi.fn(),
    sync: vi.fn(),
    requestTask: vi.fn(),
    cancelTask: vi.fn(),
    ...overrides,
  } as Federation
}

beforeEach(() => {
  vi.restoreAllMocks()
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

function selectTab(name: string) {
  fireEvent.mouseDown(screen.getByRole('tab', { name }), { button: 0, ctrlKey: false })
}

describe('FederationLiveWorkspace', () => {
  it('labels the surface as live, not a mock', () => {
    render(<FederationLiveWorkspace federation={baseFederation()} />)
    expect(screen.getByText(/Live partner-node workspace/i)).toBeInTheDocument()
    expect(screen.queryByText(/Interactive mock/i)).not.toBeInTheDocument()
  })

  it('shows the sharing-disabled banner with the exact codes when wiring is absent', () => {
    render(<FederationLiveWorkspace federation={baseFederation({ nodesCapability: 'disabled' })} />)
    const banner = screen.getByRole('alert')
    expect(banner.textContent).toMatch(/sharing disabled on this node/i)
    expect(banner.textContent).toContain('/api/v1/node')
  })

  it('[FED-13] explains an expired certificate instead of the generic disabled banner', () => {
    render(
      <FederationLiveWorkspace
        federation={baseFederation({
          nodesCapability: 'disabled',
          nodesError: new FederationApiError('CERTIFICATE_EXPIRED', 503),
        })}
      />,
    )
    const banner = screen.getByRole('alert')
    expect(banner.textContent).toMatch(/certificate has expired/i)
    expect(banner.textContent).toContain('init-credentials --force')
    expect(banner.textContent).not.toMatch(/sharing disabled on this node/i)
  })

  it('shows each peer certificate expiry', () => {
    render(
      <FederationLiveWorkspace
        federation={baseFederation({
          peers: [
            {
              node_id: 'peer-node',
              fingerprint: 'f'.repeat(64),
              state: 'active',
              certificate_epoch: 1,
              certificate_expires_at: '2020-01-01T00:00:00Z',
            },
          ],
        })}
      />,
    )
    expect(screen.getByText(/cannot be reached until its admin renews it/)).toBeInTheDocument()
  })

  it('renders peers with epoch and revoke actions', () => {
    render(
      <FederationLiveWorkspace
        federation={baseFederation({
          peers: [
            {
              node_id: '11111111-2222-3333-4444-555555555555',
              fingerprint: 'a'.repeat(64),
              state: 'active',
              certificate_epoch: 3,
            },
          ],
        })}
      />,
    )
    expect(screen.getByText(/11111111-2222-3333-4444-555555555555/)).toBeInTheDocument()
    expect(screen.getByText(/epoch 3 · active/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Revoke' })).toBeInTheDocument()
  })

  it('renders roster with tombstone badges and revision fences', () => {
    render(
      <FederationLiveWorkspace
        federation={baseFederation({
          channelRef: { authority: 'auth-1', channel: 'chan-1' },
          snapshot: {
            authority_node_id: 'auth-1',
            channel_id: 'chan-1',
            applied_seq: 12,
            messages: [],
            participants: [
              { principal: { node_id: 'b', kind: 'agent', principal_id: 'p2' }, active: true, role: 'member', revision: 2 },
              { principal: { node_id: 'a', kind: 'human', principal_id: 'p1' }, active: false, role: 'observer', revision: 5 },
            ],
          },
          roster: [
            { principal: { node_id: 'a', kind: 'human', principal_id: 'p1' }, active: false, role: 'observer', revision: 5 },
            { principal: { node_id: 'b', kind: 'agent', principal_id: 'p2' }, active: true, role: 'member', revision: 2 },
          ],
        })}
      />,
    )
    selectTab('Shared channel')
    expect(screen.getByText('tombstone · rev 5')).toBeInTheDocument()
    expect(screen.getByText('rev 2')).toBeInTheDocument()
    expect(screen.getByText(/applied seq 12/i)).toBeInTheDocument()
    // Only the active participant exposes removal.
    const removeButtons = screen.getAllByRole('button', { name: 'Remove' })
    expect(removeButtons).toHaveLength(1)
  })

  it('submits participant removal with the roster revision', async () => {
    const changeParticipant = vi.fn()
    const participant: ParticipantView = {
      principal: { node_id: 'b', kind: 'agent', principal_id: 'p2' },
      active: true,
      role: 'member',
      revision: 7,
    }
    render(
      <FederationLiveWorkspace
        federation={baseFederation({
          channelRef: { authority: 'auth-1', channel: 'chan-1' },
          snapshot: {
            authority_node_id: 'auth-1',
            channel_id: 'chan-1',
            applied_seq: 3,
            messages: [],
            participants: [participant],
          },
          roster: [participant],
          changeParticipant,
        })}
      />,
    )
    selectTab('Shared channel')
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }))
    expect(changeParticipant).toHaveBeenCalledWith(participant.principal, false, 'member', 7)
  })

  it('surfaces snapshot errors with their wire code', () => {
    render(
      <FederationLiveWorkspace
        federation={baseFederation({
          channelRef: { authority: 'auth-1', channel: 'chan-1' },
          snapshotError: { code: 'CHANNEL_DENIED', status: 403 } as never,
        })}
      />,
    )
    selectTab('Shared channel')
    expect(screen.getByText(/CHANNEL_DENIED/)).toBeInTheDocument()
  })
})

describe('FederationLiveWorkspace — shared channel', () => {
  it('offers binding-based channel selection when the node exposes bindings', () => {
    const setChannelRef = vi.fn()
    render(
      <FederationLiveWorkspace
        federation={baseFederation({
          bindings: [
            {
              authority_node_id: 'auth-1',
              channel_id: 'chan-1',
              local_room_id: 'room-9',
              last_seq: 4,
              applied_seq: 3,
            },
          ],
          setChannelRef,
        })}
      />,
    )
    selectTab('Shared channel')
    expect(screen.getByText(/bindings on this node/i)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /auth-1… \/ chan-1…/i }))
    expect(setChannelRef).toHaveBeenCalledWith({
      authority: 'auth-1',
      channel: 'chan-1',
      localRoomId: 'room-9',
      senderNodeId: undefined,
      grantEpoch: undefined,
    })
  })

  it('keeps the manual-entry hint when the node lists no bindings', () => {
    render(<FederationLiveWorkspace federation={baseFederation()} />)
    selectTab('Shared channel')
    expect(
      screen.getByText(/this node lists no bindings/i),
    ).toBeInTheDocument()
  })

  it('[#889] keeps connection and shared channel, and leaves task handoff to the shared room', () => {
    render(<FederationLiveWorkspace federation={baseFederation()} />)
    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual(['Connection', 'Shared channel'])
    expect(screen.queryByRole('tab', { name: /Task handoff/ })).not.toBeInTheDocument()
  })

  it('[#889] points people who want to run their own agents to Machines', () => {
    render(<FederationLiveWorkspace federation={baseFederation()} />)
    expect(screen.getByRole('link', { name: 'Machines' })).toHaveAttribute('href', '/admin/machines')
  })

  it('[#889] binds a local room picked from the room list, skipping DMs and rooms already shared', () => {
    roomsState.projects = [{ id: 'p1', name: 'Design' }]
    roomsState.rooms = {
      p1: [
        { id: 'room-1', name: 'review', project_id: 'p1', is_dm: false } as Room,
        { id: 'room-2', name: 'already-shared', project_id: 'p1', is_dm: false, shared_channel: { authority_node_id: 'a', channel_id: 'c' } } as Room,
        { id: 'dm-1', name: 'DM: helper', project_id: null, is_dm: true } as Room,
      ],
    }
    const bindChannel = vi.fn()
    render(<FederationLiveWorkspace federation={baseFederation({ bindChannel })} />)
    selectTab('Shared channel')
    fireEvent.change(screen.getByLabelText('Authority node ID (UUID)'), { target: { value: 'auth-1' } })
    fireEvent.change(screen.getByLabelText('Channel ID (UUID)'), { target: { value: 'chan-1' } })
    const picker = screen.getByLabelText('Bind a local room (authority side)')
    const options = Array.from(picker.querySelectorAll('option')).map((option) => option.textContent)
    expect(options).toContain('Design / review')
    expect(options).not.toContain('Design / already-shared')
    expect(options).not.toContain('DM: helper')
    const bind = screen.getByRole('button', { name: 'Bind' })
    expect(bind).toBeDisabled()
    fireEvent.change(picker, { target: { value: 'room-1' } })
    fireEvent.click(bind)
    expect(bindChannel).toHaveBeenCalledWith({ authority_node_id: 'auth-1', channel_id: 'chan-1', local_room_id: 'room-1' })
    roomsState.projects = []
    roomsState.rooms = {}
  })

  it('[#889] opens the bound room from the shared channel tab', () => {
    render(
      <FederationLiveWorkspace
        federation={baseFederation({ channelRef: { authority: 'auth-1', channel: 'chan-1', localRoomId: 'room-9' } })}
      />,
    )
    selectTab('Shared channel')
    expect(screen.getByRole('link', { name: 'Open shared conversation' })).toHaveAttribute('href', '/rooms/room-9')
  })
})

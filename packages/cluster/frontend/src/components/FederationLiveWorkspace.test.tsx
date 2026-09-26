// @vitest-environment jsdom
// UI tests for the live federation workspace (#593 / task #33). Verifies the
// honest-state surfaces: disabled banner when the node has no federation
// wiring, roster with tombstone revisions, submission tracking, and the
// command-field gating on the task tab.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { cleanup, render as renderView, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import '@testing-library/jest-dom/vitest'
import FederationLiveWorkspace from './FederationLiveWorkspace'
import type { ParticipantView } from '@/lib/federationApi'
import type { useFederation } from '@/hooks/useFederation'

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
    expect(screen.getByText(/Connected federation workspace/i)).toBeInTheDocument()
    expect(screen.queryByText(/Interactive mock/i)).not.toBeInTheDocument()
  })

  it('shows the sharing-disabled banner with the exact codes when wiring is absent', () => {
    render(<FederationLiveWorkspace federation={baseFederation({ nodesCapability: 'disabled' })} />)
    const banner = screen.getByRole('alert')
    expect(banner.textContent).toMatch(/sharing disabled on this node/i)
    expect(banner.textContent).toContain('/api/v1/node')
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
    selectTab('2. Shared channel')
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
    selectTab('2. Shared channel')
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }))
    expect(changeParticipant).toHaveBeenCalledWith(participant.principal, false, 'member', 7)
  })

  it('gates task submission until follower command fields are set', () => {
    render(
      <FederationLiveWorkspace
        federation={baseFederation({
          channelRef: { authority: 'auth-1', channel: 'chan-1' },
        })}
      />,
    )
    selectTab('3. Task handoff')
    const statusBox = screen
      .getAllByRole('status')
      .find((node) => node.textContent?.includes('grant epoch'))
    expect(statusBox).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Submit task request' })).toBeDisabled()
  })

  it('lists submissions with receipt detail', () => {
    render(
      <FederationLiveWorkspace
        federation={baseFederation({
          channelRef: { authority: 'auth-1', channel: 'chan-1', senderNodeId: 'node-self', grantEpoch: 1 },
          submissions: [
            {
              request_id: 'r1',
              state: 'confirmed',
              receipt: { state: 'applied', task_status: 'todo', process_state: 'not_started', revision: 1, seq: 4 },
              error_code: null,
              authority: 'auth-1',
              channel: 'chan-1',
              kind: 'task.request',
              createdAt: 1,
            },
            {
              request_id: 'r2',
              state: 'failed',
              receipt: null,
              error_code: 'SCOPE_DENIED',
              authority: 'auth-1',
              channel: 'chan-1',
              kind: 'task.cancel',
              createdAt: 2,
            },
          ],
        })}
      />,
    )
    selectTab('3. Task handoff')
    expect(screen.getByText('confirmed')).toBeInTheDocument()
    expect(screen.getByText(/receipt applied · task todo · process not_started/)).toBeInTheDocument()
    expect(screen.getByText(/SCOPE_DENIED/)).toBeInTheDocument()
    // The sync affordance only appears for pending work.
    expect(screen.queryByRole('button', { name: /sync now/i })).not.toBeInTheDocument()
  })

  it('exposes sync for unconfirmed submissions', () => {
    render(
      <FederationLiveWorkspace
        federation={baseFederation({
          channelRef: { authority: 'auth-1', channel: 'chan-1', senderNodeId: 'node-self', grantEpoch: 1 },
          submissions: [
            {
              request_id: 'r1',
              state: 'unconfirmed',
              receipt: null,
              error_code: null,
              authority: 'auth-1',
              channel: 'chan-1',
              kind: 'task.request',
              createdAt: 1,
            },
          ],
        })}
      />,
    )
    selectTab('3. Task handoff')
    expect(screen.getByRole('button', { name: /sync now/i })).toBeInTheDocument()
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
    selectTab('2. Shared channel')
    expect(screen.getByText(/CHANNEL_DENIED/)).toBeInTheDocument()
  })
})

describe('FederationLiveWorkspace — task #35 delegation states', () => {
  it('renders the confirmed delegation vocabulary including cancel/unknown guidance', () => {
    render(
      <FederationLiveWorkspace
        federation={baseFederation({
          channelRef: { authority: 'auth-1', channel: 'chan-1', localRoomId: 'room-9' },
          delegations: [
            {
              authority_node_id: 'auth-1',
              channel_id: 'chan-1',
              delegation_id: 'd1',
              task_id: 't1',
              source_message_id: 'm1',
              requester: { node_id: 'auth-1', kind: 'human', principal_id: 'u1' },
              executor: { node_id: 'b', agent_id: 'g1' },
              execution_id: 'x1',
              revision: 2,
              state: 'running',
              process_state: 'running',
              task_status: 'in_progress',
            },
            {
              authority_node_id: 'auth-1',
              channel_id: 'chan-1',
              delegation_id: 'd2',
              task_id: 't2',
              source_message_id: 'm2',
              requester: { node_id: 'auth-1', kind: 'human', principal_id: 'u1' },
              executor: { node_id: 'b', agent_id: 'g2' },
              execution_id: null,
              revision: 2,
              state: 'unknown',
              process_state: 'unknown',
              task_status: 'blocked',
            },
          ],
        })}
      />,
    )
    fireEvent.mouseDown(screen.getByRole('tab', { name: '3. Task handoff' }), { button: 0, ctrlKey: false })
    expect(screen.getByText('running')).toBeInTheDocument()
    expect(screen.getByText('unknown')).toBeInTheDocument()
    expect(screen.getByText(/automatic retry stays blocked/i)).toBeInTheDocument()
    expect(
      screen.queryByText(/fine-grained delegation state.*pending backend work/i),
    ).not.toBeInTheDocument()
  })

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
    selectTab('2. Shared channel')
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
    selectTab('2. Shared channel')
    expect(
      screen.getByText(/this node lists no bindings/i),
    ).toBeInTheDocument()
  })
})

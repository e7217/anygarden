export type NodeConnectionState = 'pending' | 'accepted' | 'rejected' | 'expired' | 'revoked'
export type NodeReachability = 'online' | 'offline'

export interface FederationNode {
  id: string
  name: string
  connection: NodeConnectionState
  acknowledgement: 'not_required' | 'waiting' | 'confirmed'
  reachability: NodeReachability
  direction: 'sent' | 'received'
  sharedChannels: number
}

export interface FederatedAgent {
  id: string
  nodeId: string
  name: string
  origin: 'local' | 'remote'
  available: boolean
  published: boolean
  participant: {
    active: boolean
    role: 'observer' | 'member' | 'admin' | 'owner'
    revision: number
  }
  executionAllowed: boolean
}

export interface ParticipantChangedEvent {
  seq: number
  kind: 'participant.changed'
  principal: { nodeId: string; agentId: string }
  active: boolean
  role: FederatedAgent['participant']['role']
  revision: number
}

export type DelegationState =
  | 'draft'
  | 'unconfirmed'
  | 'requested'
  | 'accepted'
  | 'running'
  | 'completed'
  | 'failed'
  | 'rejected'
  | 'cancel_requested'
  | 'cancelled'
  | 'unknown'

export type ExecutionProcessState =
  | 'not_started'
  | 'unknown'
  | 'running'
  | 'finished'
  | 'stopped'

export type FederatedTaskStatus = 'todo' | 'in_progress' | 'blocked' | 'done' | 'failed'

export interface FederationScenario {
  localNode: FederationNode
  remoteNode: FederationNode
  channelOwnerNodeId: string
  agents: FederatedAgent[]
  delegationState: DelegationState
  processState: ExecutionProcessState
  taskStatus: FederatedTaskStatus
}

/** Presentation-only intents. A future #590–592 adapter can implement these. */
export type FederationIntent =
  | { type: 'accept_node_invite'; nodeId: string }
  | { type: 'confirm_peer_receipt'; nodeId: string }
  | { type: 'revoke_node_access'; nodeId: string }
  | { type: 'request_delegation'; agentId: string; nodeId: string }
  | { type: 'confirm_authority_commit' }
  | { type: 'accept_delegation'; agentId: string; nodeId: string }
  | { type: 'reject_delegation'; agentId: string; nodeId: string }
  | { type: 'request_cancel' }
  | { type: 'confirm_stop' }
  | { type: 'mark_execution_unknown' }
  | { type: 'report_known_failure'; errorCode: 'ENGINE_ERROR' }
  | { type: 'set_mock_reachability'; nodeId: string; reachability: NodeReachability }

export const initialFederationScenario: FederationScenario = {
  localNode: {
    id: 'node-garden',
    name: 'Garden studio',
    connection: 'accepted',
    acknowledgement: 'confirmed',
    reachability: 'online',
    direction: 'sent',
    sharedChannels: 2,
  },
  remoteNode: {
    id: 'node-orchard',
    name: 'Orchard lab',
    connection: 'pending',
    acknowledgement: 'not_required',
    reachability: 'online',
    direction: 'received',
    sharedChannels: 0,
  },
  channelOwnerNodeId: 'node-garden',
  agents: [
    {
      id: 'planner',
      nodeId: 'node-garden',
      name: 'Planner',
      origin: 'local',
      available: true,
      published: true,
      participant: { active: true, role: 'owner', revision: 1 },
      executionAllowed: true,
    },
    {
      id: 'builder',
      nodeId: 'node-orchard',
      name: 'Builder',
      origin: 'remote',
      available: true,
      published: true,
      participant: { active: true, role: 'member', revision: 3 },
      executionAllowed: true,
    },
    {
      id: 'private-reviewer',
      nodeId: 'node-orchard',
      name: 'Private reviewer',
      origin: 'remote',
      available: true,
      published: false,
      participant: { active: false, role: 'observer', revision: 1 },
      executionAllowed: false,
    },
  ],
  delegationState: 'draft',
  processState: 'not_started',
  taskStatus: 'todo',
}

export const delegationLabels: Record<DelegationState, string> = {
  draft: 'Ready to request',
  unconfirmed: 'Submitted locally — not confirmed by the channel owner',
  requested: 'Waiting for the execution node',
  accepted: 'Accepted — execution has not started',
  running: 'Running on the remote node',
  completed: 'Completion confirmed by the channel owner',
  failed: 'Execution failed and termination is confirmed',
  rejected: 'Request declined by the execution node',
  cancel_requested: 'Stop requested — execution may still be active',
  cancelled: 'Stopped and cancellation confirmed',
  unknown: 'Execution outcome is unknown — do not retry',
}

/**
 * Apply the display-only participant projection from PR #596.
 * Old/duplicate revisions are ignored, so an inactive tombstone cannot be
 * revived by a delayed event. Grants and execution consent are intentionally
 * untouched: participant.changed is roster state, not authorization.
 */
export function applyParticipantChanged(
  scenario: FederationScenario,
  event: ParticipantChangedEvent,
): FederationScenario {
  const index = scenario.agents.findIndex(
    (agent) => agent.nodeId === event.principal.nodeId && agent.id === event.principal.agentId,
  )
  if (index < 0) return scenario

  const current = scenario.agents[index]
  if (event.revision <= current.participant.revision) return scenario
  if (event.revision !== current.participant.revision + 1) return scenario

  const agents = [...scenario.agents]
  agents[index] = {
    ...current,
    participant: {
      active: event.active,
      role: event.role,
      revision: event.revision,
    },
  }
  return { ...scenario, agents }
}

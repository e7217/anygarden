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
  shared: boolean
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
      shared: true,
    },
    {
      id: 'builder',
      nodeId: 'node-orchard',
      name: 'Builder',
      origin: 'remote',
      available: true,
      shared: true,
    },
    {
      id: 'private-reviewer',
      nodeId: 'node-orchard',
      name: 'Private reviewer',
      origin: 'remote',
      available: true,
      shared: false,
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

/**
 * Types mirroring the backend ``/api/v1/graph`` payload.
 * Keep these in lock-step with ``packages/cluster/doorae/api/v1/graph.py``.
 */

export type NodeKind = 'user' | 'machine' | 'agent' | 'room' | 'project'

export type EdgeKind =
  | 'owns'
  | 'places'
  | 'participates'
  | 'parent_of'
  | 'represents'

export type Scope = 'personal' | 'global' | 'auto'

export interface UserNodeData {
  is_admin: boolean
  is_anonymous: boolean
  display_name: string | null
}

export interface MachineNodeData {
  status: string // "online" | "offline" | "draining"
  hostname: string
  daemon_version: string | null
  owner_user_id: string
  agent_count: number
}

export interface AgentNodeData {
  engine: string
  actual_state: string
  desired_state: string
  model: string | null
  placed_on_machine_id: string | null
  last_heartbeat_at: string | null
  last_crash_reason: string | null
}

export interface RoomNodeData {
  is_dm: boolean
  project_id: string
  parent_room_id: string | null
  participant_count: number
  representative_agent_id: string | null
}

export interface ProjectNodeData {
  description: string | null
}

export type NodeData =
  | UserNodeData
  | MachineNodeData
  | AgentNodeData
  | RoomNodeData
  | ProjectNodeData
  | Record<string, unknown>

export interface GraphNode {
  id: string
  kind: NodeKind
  label: string
  data: NodeData
}

export interface GraphEdge {
  id: string
  source: string
  target: string
  kind: EdgeKind
  data?: { actor?: 'user' | 'agent' } & Record<string, unknown>
}

export interface GraphResponse {
  generated_at: string
  scope: 'personal' | 'global'
  nodes: GraphNode[]
  edges: GraphEdge[]
}

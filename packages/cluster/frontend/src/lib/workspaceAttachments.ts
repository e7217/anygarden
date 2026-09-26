import type { MessageKey } from '@/i18n/messages'

export interface WorkspaceOptions {
  agent_id: string
  participant_id: string
  machine_id: string | null
  machine_name: string | null
  execution_kind: 'integrated' | 'remote' | null
  node_data_dir: string | null
  can_approve_room: boolean
  can_approve_global: boolean
  read: { supported: boolean; reason: string | null }
  write: { supported: boolean; reason: string | null }
  workspaces: Array<{ workspace_id: string; label: string; max_mode: 'read' | 'write'; expires_at: string }>
}

export function workspaceSupportMessage(reason: string | null | undefined, t: (key: MessageKey) => string): string {
  const messages: Record<string, MessageKey> = {
    agent_not_placed: 'agentSetup.workspaceNotPlaced',
    machine_offline: 'agentSetup.workspaceMachineOffline',
    workspace_engine_unsupported: 'agentSetup.workspaceEngineUnsupported',
    workspace_read_requires_restricted: 'agentSetup.workspaceRestrictedRequired',
    workspace_write_adapter_unavailable: 'agentSetup.workspaceWriteUnavailable',
    workspace_root_or_audit_capability_missing: 'agentSetup.workspaceMachineUnsupported',
    workspace_receipt_signing_unavailable: 'agentSetup.workspaceMachineUnsupported',
  }
  return t(reason && messages[reason] ? messages[reason] : 'agentSetup.workspaceSupportUnknown')
}

export function workspaceCliPrefix(kind: 'integrated' | 'remote' | null, nodeDataDir?: string | null): string {
  const directory = nodeDataDir
    ? `'${nodeDataDir.replace(/'/g, `'"'"'`)}'`
    : '"${ANYGARDEN_NODE_DATA_DIR:?Set ANYGARDEN_NODE_DATA_DIR to the running node data directory}"'
  return kind === 'integrated'
    ? `anygarden-machine workspace --node-data-dir ${directory}`
    : 'anygarden-machine workspace'
}

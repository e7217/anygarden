import type { MessageKey } from '@/i18n/messages'

export type AgentStage = 'preparing' | 'using_tool' | 'writing' | 'waiting_peers'

/** #762 — how many of the peers an ``ask_peer`` caller waits on have answered. */
export interface PeerProgress {
  done: number
  total: number
}

const AGENT_STAGES: readonly AgentStage[] = ['preparing', 'using_tool', 'writing', 'waiting_peers']

export function isAgentStage(value: unknown): value is AgentStage {
  return AGENT_STAGES.includes(value as AgentStage)
}

export function peerProgressFrom(data: { waiting_done?: unknown; waiting_total?: unknown }): PeerProgress | null {
  const { waiting_done: done, waiting_total: total } = data
  return typeof done === 'number' && typeof total === 'number' ? { done, total } : null
}

type Translate = (key: MessageKey, vars?: Record<string, string | number>) => string

/** The localized stage text shown after a typing participant's name. */
export function stageLabel(t: Translate, stage: AgentStage, progress?: PeerProgress): string {
  switch (stage) {
    case 'preparing':
      return t('chat.stagePreparing')
    case 'using_tool':
      return t('chat.stageUsingTool')
    case 'writing':
      return t('chat.stageWriting')
    case 'waiting_peers':
      return progress
        ? t('chat.stageWaitingPeersCount', { done: progress.done, total: progress.total })
        : t('chat.stageWaitingPeers')
  }
}

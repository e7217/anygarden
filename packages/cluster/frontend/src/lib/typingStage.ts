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

/** #880 — the closed kinds of work an agent reports while typing. */
export type AgentActivity =
  | 'command' | 'file_change' | 'file_read' | 'anygarden_tool' | 'tool' | 'web_search' | 'thinking'

const AGENT_ACTIVITIES: readonly AgentActivity[] = [
  'command', 'file_change', 'file_read', 'anygarden_tool', 'tool', 'web_search', 'thinking',
]

/** What an agent's typing heartbeat says beyond its stage. */
export interface AgentWorkDetail {
  activity?: AgentActivity
  /** Which of this turn's runs of ``activity`` this is (1-based). */
  seq?: number
  /** The latest sentence the agent wrote before more work. */
  narration?: string
}

export function workDetailFrom(data: {
  activity?: unknown
  activity_seq?: unknown
  narration?: unknown
}): AgentWorkDetail | null {
  const detail: AgentWorkDetail = {}
  if (AGENT_ACTIVITIES.includes(data.activity as AgentActivity)) {
    detail.activity = data.activity as AgentActivity
    if (typeof data.activity_seq === 'number' && Number.isInteger(data.activity_seq) && data.activity_seq > 0) {
      detail.seq = data.activity_seq
    }
  }
  if (typeof data.narration === 'string' && data.narration.trim()) detail.narration = data.narration.trim()
  return Object.keys(detail).length ? detail : null
}

const ACTIVITY_KEYS: Record<AgentActivity, MessageKey> = {
  command: 'chat.activityCommand',
  file_change: 'chat.activityFileChange',
  file_read: 'chat.activityFileRead',
  anygarden_tool: 'chat.activityAnygardenTool',
  tool: 'chat.activityTool',
  web_search: 'chat.activityWebSearch',
  thinking: 'chat.activityThinking',
}

/** The running work if the agent named it, else its stage label. */
export function workLabel(
  t: Translate,
  stage: AgentStage | undefined,
  detail?: AgentWorkDetail | null,
  progress?: PeerProgress,
): string | undefined {
  if (detail?.activity) {
    const label = t(ACTIVITY_KEYS[detail.activity])
    return detail.seq && detail.seq > 1
      ? t('chat.activityWithSeq', { label, seq: detail.seq })
      : t('chat.activityPlain', { label })
  }
  return stage ? stageLabel(t, stage, progress) : undefined
}

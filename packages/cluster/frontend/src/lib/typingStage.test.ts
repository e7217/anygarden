import { describe, expect, it } from 'vitest'
import { stageLabel } from './typingStage'

const t = (key: string, vars?: Record<string, string | number>) =>
  vars ? `${key}(${Object.values(vars).join('/')})` : key

describe('stageLabel', () => {
  it('shows how many peers an ask_peer caller is still waiting on', () => {
    expect(stageLabel(t, 'waiting_peers', { done: 1, total: 2 })).toBe('chat.stageWaitingPeersCount(1/2)')
  })

  it('omits counts when the server sent none', () => {
    expect(stageLabel(t, 'waiting_peers')).toBe('chat.stageWaitingPeers')
  })
})

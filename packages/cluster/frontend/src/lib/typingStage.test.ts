import { describe, expect, it } from 'vitest'
import { stageLabel, workDetailFrom, workLabel } from './typingStage'

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

describe('[ENG-10] agent work detail', () => {
  it('reads only closed activities, positive ordinals and non-empty narration', () => {
    expect(workDetailFrom({ activity: 'command', activity_seq: 3, narration: '  Checking.  ' }))
      .toEqual({ activity: 'command', seq: 3, narration: 'Checking.' })
    expect(workDetailFrom({ activity: 'rm -rf', activity_seq: 0, narration: ' ' })).toBeNull()
    expect(workDetailFrom({})).toBeNull()
  })

  it('names the running work instead of the coarse stage', () => {
    expect(workLabel(t, 'using_tool', { activity: 'command', seq: 3 }))
      .toBe('chat.activityWithSeq(chat.activityCommand/3)')
    // The first of a kind needs no ordinal.
    expect(workLabel(t, 'using_tool', { activity: 'file_change', seq: 1 })).toBe('chat.activityPlain(chat.activityFileChange)')
    expect(workLabel(t, 'preparing', { narration: 'Checking.' })).toBe('chat.stagePreparing')
    expect(workLabel(t, undefined, undefined)).toBeUndefined()
  })
})

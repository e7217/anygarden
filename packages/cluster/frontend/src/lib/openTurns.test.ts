import { describe, it, expect } from 'vitest'
import { activeTurnByParticipant, placeWorkingParticipants, threadTurnIds, type OpenTurn } from './openTurns'

const turn = (request_id: string, participant_id: string, thread_root_id: string | null, state = 'leased'): OpenTurn =>
  ({ request_id, participant_id, thread_root_id, state })

describe('openTurns', () => {
  it('[ENG-09] prefers the running turn over an older queued one', () => {
    const active = activeTurnByParticipant([turn('a', 'pm', null, 'pending'), turn('b', 'pm', 'root-1')])
    expect(active.get('pm')?.request_id).toBe('b')
  })

  it('[ENG-09] places thread work under its thread and the rest in the main timeline', () => {
    const turns = [turn('a', 'pm', 'root-1'), turn('b', 'local', 'root-1'), turn('c', 'wiki', null)]
    const { main, byThread } = placeWorkingParticipants(['pm', 'local', 'wiki', 'me'], turns, () => true)
    expect(main).toEqual(['wiki', 'me'])
    expect(byThread.get('root-1')).toEqual(['pm', 'local'])
  })

  it('[ENG-09] keeps work in the main timeline when its thread is not on screen', () => {
    const { main, byThread } = placeWorkingParticipants(['pm'], [turn('a', 'pm', 'root-1')], () => false)
    expect(main).toEqual(['pm'])
    expect(byThread.size).toBe(0)
  })

  it('[ENG-09] collects every open turn in a thread, optionally for one agent', () => {
    const turns = [turn('a', 'pm', 'root-1', 'pending'), turn('b', 'local', 'root-1'), turn('c', 'pm', null)]
    expect(threadTurnIds(turns, 'root-1')).toEqual(['a', 'b'])
    expect(threadTurnIds(turns, 'root-1', 'pm')).toEqual(['a'])
  })
})

import { describe, expect, it } from 'vitest'
import { goalDrafter, parseDraftFailed, parseDraftRequest, routineDrafter } from './drafts'
import type { Participant } from '@/lib/participants'
import type { Room } from '@/hooks/useRooms'

const agent = (id: string, agentId: string): Participant => ({ id, display_name: id, kind: 'agent', agent_id: agentId })
const room = (lead: string | null) => ({ id: 'r', representative_agent_id: lead }) as unknown as Room

describe('draft drafter rules (#947, #948)', () => {
  const two = { p1: agent('p1', 'a1'), p2: agent('p2', 'a2'), u: { id: 'u', display_name: 'me', kind: 'user', user_id: 'u1' } as Participant }

  it('[WSP-07] uses the named agent, else the only agent, else the lead', () => {
    expect(routineDrafter(room(null), two, 'p2')?.id).toBe('p2')
    expect(routineDrafter(room(null), { p1: agent('p1', 'a1') }, null)?.id).toBe('p1')
    expect(routineDrafter(room('a2'), two, null)?.id).toBe('p2')
  })

  it('[WSP-07] asks for an agent when several share a room without a lead', () => {
    expect(routineDrafter(room(null), two, null)).toBeNull()
  })

  it('[WSP-06] has the lead draft every goal', () => {
    expect(goalDrafter(room('a1'), two)?.id).toBe('p1')
    expect(goalDrafter(room(null), two)).toBeNull()
  })

  it('reads the request and failure notices', () => {
    expect(parseDraftRequest({ draft_request: { id: 'd', kind: 'goal', description: 'x', drafter_participant_id: 'p1' } }))
      .toEqual({ id: 'd', kind: 'goal', description: 'x', drafter_participant_id: 'p1' })
    expect(parseDraftRequest({ draft_request: { id: 'd', kind: 'task' } })).toBeNull()
    expect(parseDraftFailed({ draft_request_failed: { id: 'd', kind: 'routine', description: 'y', assignee_agent_id: 'a1' } }))
      .toMatchObject({ kind: 'routine', assignee_agent_id: 'a1' })
  })
})

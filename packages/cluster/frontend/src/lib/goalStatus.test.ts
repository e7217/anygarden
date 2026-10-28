import { describe, it, expect } from 'vitest'
import { parseGoalStatus } from './goalStatus'

describe('parseGoalStatus', () => {
  it('reads the notice block', () => {
    expect(parseGoalStatus({
      goal_status: { campaign_id: 'c1', title: 'Ideas', status: 'blocked', previous: 'active', detail: 'Fix it.' },
    })).toEqual({ campaign_id: 'c1', title: 'Ideas', status: 'blocked', detail: 'Fix it.', report: null, stopReason: null })
  })

  it('treats an empty detail as none', () => {
    expect(parseGoalStatus({
      goal_status: { campaign_id: 'c1', title: 'Ideas', status: 'achieved', detail: null },
    })?.detail).toBeNull()
  })

  it('reads the report request that asks the lead to report (#964) [CMP-17]', () => {
    expect(parseGoalStatus({
      goal_status: { campaign_id: 'c1', title: 'Ideas', status: 'awaiting_confirmation', detail: null },
      goal_report: { campaign_id: 'c1', reason: 'confirmation', lead_participant_id: 'p1' },
    })?.report).toEqual({ reason: 'confirmation', leadParticipantId: 'p1' })
    expect(parseGoalStatus({
      goal_status: { campaign_id: 'c1', title: 'Ideas', status: 'active', detail: null },
      goal_report: { reason: 'progress', lead_participant_id: null },
    })).toMatchObject({ status: 'active', report: { reason: 'progress', leadParticipantId: null } })
    expect(parseGoalStatus({
      goal_status: { campaign_id: 'c1', title: 'Ideas', status: 'blocked', detail: null },
      goal_report: { reason: 'nonsense' },
    })?.report).toBeNull()
  })

  it('reads a stop reason code only for a stopped goal (#969) [CMP-16]', () => {
    const stopped = (stop_reason: unknown, status = 'stopped') => parseGoalStatus({
      goal_status: { campaign_id: 'c1', title: 'Ideas', status, detail: null, stop_reason },
    })?.stopReason
    expect(stopped('user')).toBe('user')
    expect(stopped('room_archived')).toBe('room_archived')
    expect(stopped('something_new')).toBeNull()
    expect(stopped('user', 'achieved')).toBeNull()
  })

  it('ignores other messages and unknown statuses', () => {
    expect(parseGoalStatus(undefined)).toBeNull()
    expect(parseGoalStatus({ task_assignment: {} })).toBeNull()
    expect(parseGoalStatus({ goal_status: { campaign_id: 'c1', title: 'x', status: 'active' } })).toBeNull()
  })
})

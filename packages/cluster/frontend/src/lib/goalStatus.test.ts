import { describe, it, expect } from 'vitest'
import { parseGoalStatus } from './goalStatus'

describe('parseGoalStatus', () => {
  it('reads the notice block', () => {
    expect(parseGoalStatus({
      goal_status: { campaign_id: 'c1', title: 'Ideas', status: 'blocked', previous: 'active', detail: 'Fix it.' },
    })).toEqual({ campaign_id: 'c1', title: 'Ideas', status: 'blocked', detail: 'Fix it.' })
  })

  it('treats an empty detail as none', () => {
    expect(parseGoalStatus({
      goal_status: { campaign_id: 'c1', title: 'Ideas', status: 'achieved', detail: null },
    })?.detail).toBeNull()
  })

  it('ignores other messages and unknown statuses', () => {
    expect(parseGoalStatus(undefined)).toBeNull()
    expect(parseGoalStatus({ task_assignment: {} })).toBeNull()
    expect(parseGoalStatus({ goal_status: { campaign_id: 'c1', title: 'x', status: 'active' } })).toBeNull()
  })
})

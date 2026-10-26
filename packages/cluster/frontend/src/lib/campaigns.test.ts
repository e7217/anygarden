import { describe, expect, it } from 'vitest'
import { campaignActions, campaignStatusTone, type CampaignStatus } from './campaigns'

describe('campaignStatusTone', () => {
  it.each<[CampaignStatus, string]>([
    ['active', 'neutral'],
    ['paused', 'warning'],
    ['blocked', 'warning'],
    ['plateau', 'warning'],
    ['awaiting_confirmation', 'warning'],
    ['achieved', 'success'],
    ['deadline_reached', 'neutral'],
    ['budget_exhausted', 'neutral'],
    ['stopped', 'neutral'],
  ])('%s → %s', (status, tone) => {
    expect(campaignStatusTone(status)).toBe(tone)
  })
})

describe('campaignActions', () => {
  it('follows the server transitions', () => {
    expect(campaignActions('active')).toEqual({ pause: true, resume: false, stop: true, decide: false, extend: false })
    expect(campaignActions('paused')).toMatchObject({ pause: false, resume: true, stop: true })
    expect(campaignActions('blocked')).toMatchObject({ resume: true, stop: true })
    expect(campaignActions('plateau')).toMatchObject({ resume: true, stop: true })
    expect(campaignActions('awaiting_confirmation')).toEqual({ pause: false, resume: false, stop: true, decide: true, extend: false })
    expect(campaignActions('deadline_reached')).toEqual({ pause: false, resume: false, stop: false, decide: false, extend: true })
    expect(campaignActions('budget_exhausted')).toMatchObject({ extend: true, stop: false })
    expect(campaignActions('achieved')).toEqual({ pause: false, resume: false, stop: false, decide: false, extend: false })
    expect(campaignActions('stopped')).toEqual({ pause: false, resume: false, stop: false, decide: false, extend: false })
  })
})

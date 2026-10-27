import { describe, expect, it } from 'vitest'
import { campaignErrorMessage, campaignStatusDetail } from './campaignErrors'
import { CampaignApiError } from '@/lib/campaigns'
import { en } from '@/i18n/messages'
import type { MessageKey } from '@/i18n/messages'

const t = (key: MessageKey, values: Record<string, string | number> = {}) =>
  Object.entries(values).reduce<string>((text, [k, v]) => text.split(`{{${k}}}`).join(String(v)), en[key])

describe('campaignErrorMessage', () => {
  it('maps codes and reasons to catalog text', () => {
    expect(campaignErrorMessage(t, new CampaignApiError('x', 403, 'CAMPAIGN_FORBIDDEN', null), 'change')).toBe(en['campaigns.error.forbidden'])
    expect(campaignErrorMessage(t, new CampaignApiError('x', 403, null, null), 'create')).toBe(en['campaigns.error.createForbidden'])
    expect(campaignErrorMessage(t, new CampaignApiError('x', 422, 'CAMPAIGN_INVALID', 'EXTEND_DOES_NOT_REOPEN'), 'change')).toBe(en['campaigns.error.doesNotReopen'])
    expect(campaignErrorMessage(t, new CampaignApiError('x', 422, 'CAMPAIGN_INVALID', 'INVALID_BUDGET'), 'change', { current: '100' }))
      .toBe('The new token budget must be larger than the current one (100).')
    expect(campaignErrorMessage(t, new CampaignApiError('x', 409, 'CAMPAIGN_CONFLICT', 'ILLEGAL_TRANSITION'), 'change')).toBe(en['campaigns.error.illegalTransition'])
    expect(campaignErrorMessage(t, new CampaignApiError('x', 404, 'CAMPAIGN_NOT_FOUND', null), 'change')).toBe(en['campaigns.error.notFound'])
  })

  it('falls back to the server message', () => {
    expect(campaignErrorMessage(t, new CampaignApiError('cwd escapes', 422, 'CAMPAIGN_INVALID', 'INVALID_EVALUATOR'), 'create'))
      .toBe("Couldn't start the goal: cwd escapes")
    expect(campaignErrorMessage(t, new Error('offline'), 'change')).toBe("Couldn't update the goal: offline")
  })
})

describe('campaignStatusDetail', () => {
  // The exact sentences of campaigns/lifecycle.py and campaigns/scheduler.py.
  it('translates the server sentences it knows', () => {
    expect(campaignStatusDetail(t, 'The pinned evaluator files changed; restore them or update the pins.')).toBe(en['campaigns.detail.pinMismatch'])
    expect(campaignStatusDetail(t, "The campaign's agent is no longer a participant of the room.")).toBe(en['campaigns.detail.agentNotInRoom'])
    expect(campaignStatusDetail(t, 'The evaluation failed 3 times in a row; check the evaluator.'))
      .toBe('The evaluation failed 3 times in a row. Check the evaluator.')
    expect(campaignStatusDetail(t, 'The agent reported: dataset missing. To unblock: upload data.csv'))
      .toBe('The agent reported: dataset missing. To unblock: upload data.csv')
    expect(campaignStatusDetail(t, 'The agent reported: dataset missing.')).toBe('The agent reported: dataset missing.')
  })

  it('shows unknown text as is', () => {
    expect(campaignStatusDetail(t, 'Something new')).toBe('Something new')
  })
})

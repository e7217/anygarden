import { describe, expect, it } from 'vitest'
import { buildCampaignInput, campaignFormFields, emptyCampaignDraft, firstInvalidField, type CampaignDraft } from './campaignDraft'

const NOW = new Date('2026-10-26T00:00:00Z')

function draft(overrides: Partial<CampaignDraft> = {}): CampaignDraft {
  return {
    ...emptyCampaignDraft(),
    title: 'Speed up parser',
    purpose: 'Release blocker',
    metricName: 'accuracy',
    command: 'python eval.py',
    ...overrides,
  }
}

describe('campaignFormFields (plan §3.9 condition table)', () => {
  it('[CMP-01] target mode: target required, deadline optional', () => {
    expect(campaignFormFields('target')).toEqual({ target: 'required', deadline: 'optional' })
  })
  it('[CMP-01] optimize mode: target hidden, deadline required', () => {
    expect(campaignFormFields('optimize')).toEqual({ target: 'hidden', deadline: 'required' })
  })
})

describe('buildCampaignInput', () => {
  it('[CMP-01] target mode needs a target and accepts no deadline', () => {
    expect(buildCampaignInput(draft({ mode: 'target' }), NOW).errors).toEqual({ target: 'campaigns.form.required' })
    const { input } = buildCampaignInput(draft({ mode: 'target', target: '0.9' }), NOW)
    expect(input?.metric).toEqual({ name: 'accuracy', direction: 'maximize', target: 0.9 })
    expect(input).not.toHaveProperty('deadline_at')
  })

  it('[CMP-01] optimize mode needs a future deadline and never sends a target', () => {
    expect(buildCampaignInput(draft({ mode: 'optimize' }), NOW).errors).toEqual({ deadline: 'campaigns.form.required' })
    expect(buildCampaignInput(draft({ mode: 'optimize', deadline: '2026-10-01T10:00' }), NOW).errors)
      .toEqual({ deadline: 'campaigns.form.deadlinePast' })
    // A target typed before switching modes is dropped.
    const { input } = buildCampaignInput(draft({ mode: 'optimize', target: '0.9', deadline: '2026-11-01T10:00' }), NOW)
    expect(input?.metric).not.toHaveProperty('target')
    expect(input?.deadline_at).toBe(new Date('2026-11-01T10:00').toISOString())
  })

  it('sends the advanced defaults and only schema fields', () => {
    const { input } = buildCampaignInput(draft({ target: '1', pinPaths: 'eval.py\n\n data/ ' }), NOW)
    expect(input).toEqual({
      title: 'Speed up parser',
      purpose: 'Release blocker',
      mode: 'target',
      metric: { name: 'accuracy', direction: 'maximize', target: 1 },
      evaluator: { command: 'python eval.py', cwd: '.', timeout_sec: 600, pin_paths: ['eval.py', 'data/'], score_key: 'score' },
      budget: { max_total_tokens: 500000 },
      trusted_eval: 'none',
    })
  })

  it('[CMP-01] reports required fields, a bad budget and a bad timeout', () => {
    const { errors, input } = buildCampaignInput(
      draft({ title: ' ', purpose: '', target: 'x', budgetTokens: '0', timeoutSec: '99999' }), NOW,
    )
    expect(input).toBeNull()
    expect(errors).toEqual({
      title: 'campaigns.form.required',
      purpose: 'campaigns.form.required',
      target: 'campaigns.form.number',
      budgetTokens: 'campaigns.form.positiveInteger',
      timeoutSec: 'campaigns.form.timeoutRange',
    })
  })

  it('maps the trusted checkbox to trusted_eval external', () => {
    expect(buildCampaignInput(draft({ target: '1', trustedExternal: true }), NOW).input?.trusted_eval).toBe('external')
  })
})

describe('firstInvalidField', () => {
  it('follows the form order', () => {
    expect(firstInvalidField({ timeoutSec: 'campaigns.form.timeoutRange', purpose: 'campaigns.form.required' })).toBe('purpose')
    expect(firstInvalidField({ cwd: 'campaigns.form.required' })).toBe('cwd')
    expect(firstInvalidField({})).toBeNull()
  })
})

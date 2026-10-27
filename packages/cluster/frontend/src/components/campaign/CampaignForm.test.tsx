// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import CampaignForm from './CampaignForm'
import { CampaignApiError } from '@/lib/campaigns'
import { campaignFixture } from '@/lib/campaignFixtures'

afterEach(cleanup)

function renderForm(onSubmit = vi.fn(async () => campaignFixture())) {
  const onOpenChange = vi.fn()
  render(<CampaignForm open onOpenChange={onOpenChange} onSubmit={onSubmit} />)
  return { onSubmit, onOpenChange }
}

const fill = (label: RegExp, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } })

describe('CampaignForm', () => {
  it('[CMP-01] target mode shows a required target and an optional deadline', () => {
    renderForm()
    expect(screen.getByRole('radio', { name: /Reach a target score/ })).toBeChecked()
    expect(screen.getByLabelText('Target score')).toBeRequired()
    const deadline = screen.getByLabelText('Deadline (optional)')
    expect(deadline).not.toBeRequired()
  })

  it('[CMP-01] optimize mode hides the target and requires the deadline', () => {
    renderForm()
    fireEvent.click(screen.getByRole('radio', { name: /Improve until a deadline/ }))
    expect(screen.queryByLabelText('Target score')).toBeNull()
    expect(screen.getByLabelText('Deadline')).toBeRequired()
  })

  it('keeps advanced settings folded with their defaults', () => {
    renderForm()
    const advanced = screen.getByTestId('campaign-form-advanced')
    expect(advanced).not.toHaveAttribute('open')
    expect(screen.getByLabelText(/Working folder/)).toHaveValue('.')
    expect(screen.getByLabelText(/time limit/)).toHaveValue('600')
  })

  it('[CMP-01] shows field errors instead of submitting an incomplete campaign', async () => {
    const { onSubmit } = renderForm()
    fireEvent.click(screen.getByRole('button', { name: 'Start goal' }))
    expect(onSubmit).not.toHaveBeenCalled()
    expect(screen.getByLabelText('Target score')).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getAllByText('Required.').length).toBeGreaterThan(0)
  })

  it('submits the built input and closes', async () => {
    const { onSubmit, onOpenChange } = renderForm()
    fill(/^Title$/, 'Raise accuracy')
    fill(/Purpose/, 'Release blocker')
    fill(/Metric name/, 'accuracy')
    fill(/Target score/, '0.9')
    fill(/Evaluator command/, 'python eval.py')
    fireEvent.click(screen.getByRole('button', { name: 'Start goal' }))
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false))
    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
      mode: 'target',
      metric: { name: 'accuracy', direction: 'maximize', target: 0.9 },
      trusted_eval: 'none',
    }))
  })

  it('focuses the first invalid field after a failed submit', () => {
    renderForm()
    fireEvent.click(screen.getByRole('button', { name: 'Start goal' }))
    expect(screen.getByLabelText(/^Title$/)).toHaveFocus()
    expect(screen.getByTestId('campaign-form-advanced')).not.toHaveAttribute('open')
  })

  it('unfolds "Advanced" when one of its fields is invalid', () => {
    renderForm()
    fill(/^Title$/, 'T'); fill(/Purpose/, 'P'); fill(/Metric name/, 'm'); fill(/Target score/, '1'); fill(/Evaluator command/, 'c')
    fill(/time limit/, '0')
    fireEvent.click(screen.getByRole('button', { name: 'Start goal' }))
    expect(screen.getByTestId('campaign-form-advanced')).toHaveAttribute('open')
    expect(screen.getByLabelText(/time limit/)).toHaveFocus()
    expect(screen.getByText('Enter 1 to 21600 seconds.')).toBeInTheDocument()
  })

  it.each([
    [new CampaignApiError('Task management requires a room admin or owner', 403, null, null), 'Only a room admin or owner can start a goal'],
    [new CampaignApiError('no', 409, 'CAMPAIGN_CONFLICT', 'ROOM_ARCHIVED'), 'The room is archived'],
    [new CampaignApiError('Server exploded', 500, null, null), "Couldn't start the goal: Server exploded"],
  ])('explains a create failure (%#)', async (error, text) => {
    renderForm(vi.fn(async () => { throw error }))
    fill(/^Title$/, 'T'); fill(/Purpose/, 'P'); fill(/Metric name/, 'm'); fill(/Target score/, '1'); fill(/Evaluator command/, 'c')
    fireEvent.click(screen.getByRole('button', { name: 'Start goal' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(text)
  })

  it('explains an evaluator permission error', async () => {
    const onSubmit = vi.fn(async () => { throw new CampaignApiError('no', 403, 'CAMPAIGN_EVALUATOR_FORBIDDEN', null) })
    renderForm(onSubmit)
    fill(/^Title$/, 'T'); fill(/Purpose/, 'P'); fill(/Metric name/, 'm'); fill(/Target score/, '1'); fill(/Evaluator command/, 'c')
    fireEvent.click(screen.getByRole('button', { name: 'Start goal' }))
    expect(await screen.findByRole('alert')).toHaveTextContent("Only the owner of the lead agent's machine")
  })
})

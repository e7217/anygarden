// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import CampaignSection, { validateExtend } from './CampaignSection'
import type { Participant } from '@/lib/participants'
import { FeedbackProvider } from '@/components/feedback/FeedbackProvider'
import { attemptFixture, campaignFixture } from '@/lib/campaignFixtures'
import type { Campaign } from '@/lib/campaigns'
import type { Room } from '@/hooks/useRooms'

const mocks = vi.hoisted(() => ({
  user: { id: 'user-1', email: 'a@b.c', is_admin: false } as { id: string; email: string; is_admin: boolean } | null,
  campaigns: [] as Campaign[],
  act: vi.fn(async () => {}),
  extend: vi.fn(async () => {}),
}))

vi.mock('@/hooks/useAuth', () => ({ useAuth: () => ({ user: mocks.user, loading: false }) }))
vi.mock('@/hooks/useRoomCampaigns', () => ({
  useRoomCampaigns: () => ({
    campaigns: mocks.campaigns,
    attempts: { 'camp-1': [attemptFixture({ outcome: 'improved', dev_score: 0.82, closed_at: '2026-10-26T01:00:00Z' })] },
    loading: false,
    error: null,
    refresh: vi.fn(),
    create: vi.fn(),
    act: mocks.act,
    extend: mocks.extend,
  }),
}))

const projectRoom: Room = { id: 'room-1', name: 'r', project_id: 'p', is_dm: false, representative_agent_id: 'agent-1' }

const ownerRoster: Record<string, Participant> = {
  'part-me': { id: 'part-me', display_name: 'me', kind: 'user', user_id: 'user-1', role: 'owner' },
}

function renderSection(room: Room | null = projectRoom, participants: Record<string, Participant> = ownerRoster) {
  return render(<FeedbackProvider><CampaignSection roomId="room-1" room={room} participants={participants} /></FeedbackProvider>)
}

beforeEach(() => {
  mocks.user = { id: 'user-1', email: 'a@b.c', is_admin: false }
  mocks.campaigns = []
  mocks.act.mockClear()
  mocks.extend.mockReset()
  mocks.extend.mockImplementation(async () => {})
})
afterEach(cleanup)

describe('CampaignSection', () => {
  it('stays hidden in a room that cannot host a campaign and has none', () => {
    renderSection({ ...projectRoom, representative_agent_id: null })
    expect(screen.queryByTestId('campaign-section')).toBeNull()
  })

  it('offers to start one in a project room with a lead', () => {
    renderSection()
    expect(screen.getByRole('button', { name: 'New goal' })).toBeInTheDocument()
    expect(screen.getByText(/No goals yet/)).toBeInTheDocument()
  })

  it('shows status text, best score against target, budget and attempts', () => {
    mocks.campaigns = [campaignFixture({
      status: 'awaiting_confirmation', tokens_used: 47500,
      best: { attempt_id: 'att-1', cycle_no: 1, dev_score: 0.82, trusted_score: null, verified: false },
    })]
    renderSection()
    const card = screen.getByTestId('campaign-card-camp-1')
    const badge = within(card).getByTestId('campaign-status')
    expect(badge).toHaveTextContent('Needs your confirmation')
    expect(badge).toHaveAttribute('data-tone', 'warning')
    expect(within(card).getByTestId('campaign-score')).toHaveTextContent('accuracy · Best 0.82 · target 0.9 or higher')
    expect(within(card).getByTestId('campaign-budget')).toHaveTextContent('50% of budget used')
    expect(within(card).getByText('Cycle 1 · Improved')).toBeInTheDocument()
    expect(within(card).getByRole('button', { name: 'Confirm result' })).toBeInTheDocument()
    expect(within(card).getByRole('button', { name: 'Not yet, keep going' })).toBeInTheDocument()
  })

  it('shows the deadline for an optimize campaign', () => {
    mocks.campaigns = [campaignFixture({ mode: 'optimize', metric: { name: 'latency', direction: 'minimize' }, deadline_at: '2099-01-01T00:00:00Z' })]
    renderSection()
    expect(screen.getByTestId('campaign-score')).toHaveTextContent('improving until')
  })

  it('[CMP-20] shows the lead\'s confirm advice next to the confirm button', () => {
    mocks.campaigns = [campaignFixture({
      status: 'awaiting_confirmation',
      confirm_advice: { advice: 'hold', reason: '6 ideas repeat others; 24 are distinct.', at: '2026-10-28T08:00:00Z' },
    })]
    renderSection()
    const advice = screen.getByTestId('campaign-confirm-advice')
    expect(advice).toHaveTextContent('The lead advises not to confirm yet')
    expect(advice).toHaveTextContent('6 ideas repeat others; 24 are distinct.')
    expect(advice).toHaveAttribute('data-advice', 'hold')
  })

  it('shows no advice when the lead gave none or the goal is not waiting', () => {
    mocks.campaigns = [campaignFixture({ status: 'awaiting_confirmation' })]
    renderSection()
    expect(screen.queryByTestId('campaign-confirm-advice')).toBeNull()
    cleanup()
    mocks.campaigns = [campaignFixture({
      status: 'active',
      confirm_advice: { advice: 'confirm', reason: 'old', at: null },
    })]
    renderSection()
    expect(screen.queryByTestId('campaign-confirm-advice')).toBeNull()
  })

  it('hides actions from someone who is neither owner nor admin', () => {
    mocks.user = { id: 'someone-else', email: 'x@y.z', is_admin: false }
    mocks.campaigns = [campaignFixture()]
    renderSection()
    expect(screen.queryByRole('button', { name: 'Pause' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Stop' })).toBeNull()
  })

  it('asks in the page before stopping', async () => {
    mocks.campaigns = [campaignFixture()]
    renderSection()
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }))
    const dialog = await screen.findByRole('alertdialog').catch(() => screen.getByRole('dialog'))
    expect(dialog).toHaveTextContent('Stop this goal?')
    expect(mocks.act).not.toHaveBeenCalled()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Stop' }))
    await waitFor(() => expect(mocks.act).toHaveBeenCalledWith('camp-1', 'stop'))
  })

  it('[CMP-01] shows only the tokens used for a goal without a token budget', () => {
    mocks.campaigns = [campaignFixture({
      budget: { retro_reserve_ratio: 0.05 }, usable_tokens: null, tokens_used: 1234567,
      status: 'deadline_reached', deadline_at: '2026-10-01T00:00:00Z',
    })]
    renderSection()
    const card = screen.getByTestId('campaign-card-camp-1')
    expect(within(card).getByTestId('campaign-budget')).toHaveTextContent('1,234,567 tokens used')
    expect(within(card).getByTestId('campaign-budget')).not.toHaveTextContent('%')
    fireEvent.click(within(card).getByRole('button', { name: 'Extend' }))
    expect(screen.getByLabelText('New deadline')).toBeInTheDocument()
    expect(screen.queryByLabelText('New token budget')).toBeNull()
  })

  it('[CMP-10] offers extend only once the deadline or budget ended it', () => {
    mocks.campaigns = [campaignFixture({ status: 'budget_exhausted' })]
    renderSection()
    expect(screen.getByRole('button', { name: 'Extend' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Stop' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Extend' }))
    expect(screen.getByLabelText('New token budget')).toHaveValue('100000')
  })

  it('offers to start one only to room admins, owners and global admins', () => {
    const member = { 'part-me': { ...ownerRoster['part-me'], role: 'member' } }
    const { unmount } = renderSection(projectRoom, member)
    expect(screen.queryByTestId('campaign-section')).toBeNull()
    unmount()
    mocks.user = { id: 'user-1', email: 'a@b.c', is_admin: true }
    renderSection(projectRoom, member)
    expect(screen.getByRole('button', { name: 'New goal' })).toBeInTheDocument()
  })

  it('shows campaigns to a member without offering to start one', () => {
    mocks.campaigns = [campaignFixture()]
    renderSection(projectRoom, { 'part-me': { ...ownerRoster['part-me'], role: 'member' } })
    expect(screen.getByTestId('campaign-card-camp-1')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'New goal' })).toBeNull()
  })

  it('translates a known status detail', () => {
    mocks.campaigns = [campaignFixture({ status: 'blocked', status_detail: 'The evaluation failed 3 times in a row; check the evaluator.' })]
    renderSection()
    expect(screen.getByText('The evaluation failed 3 times in a row. Check the evaluator.')).toBeInTheDocument()
  })

  it('checks an extension before sending it', async () => {
    mocks.campaigns = [campaignFixture({ status: 'budget_exhausted' })]
    renderSection()
    fireEvent.click(screen.getByRole('button', { name: 'Extend' }))
    fireEvent.change(screen.getByLabelText('New token budget'), { target: { value: '50000' } })
    fireEvent.click(screen.getByRole('button', { name: 'Extend and resume' }))
    expect(screen.getByRole('alert')).toHaveTextContent('larger than the current one (100,000)')
    expect(mocks.extend).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText('New token budget'), { target: { value: '200000' } })
    fireEvent.click(screen.getByRole('button', { name: 'Extend and resume' }))
    await waitFor(() => expect(mocks.extend).toHaveBeenCalledWith('camp-1', { max_total_tokens: 200000 }))
  })

  it('explains a refused extension in words', async () => {
    const { CampaignApiError } = await import('@/lib/campaigns')
    mocks.extend.mockImplementation(async () => { throw new CampaignApiError('x', 422, 'CAMPAIGN_INVALID', 'EXTEND_DOES_NOT_REOPEN') })
    mocks.campaigns = [campaignFixture({ status: 'budget_exhausted' })]
    renderSection()
    fireEvent.click(screen.getByRole('button', { name: 'Extend' }))
    fireEvent.change(screen.getByLabelText('New token budget'), { target: { value: '200000' } })
    fireEvent.click(screen.getByRole('button', { name: 'Extend and resume' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('does not clear what ended the goal')
  })

  it('updates the time left as time passes', () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'Date'] })
    try {
      vi.setSystemTime(new Date('2026-10-26T00:00:00Z'))
      mocks.campaigns = [campaignFixture({ deadline_at: '2026-10-26T00:30:00Z' })]
      renderSection()
      expect(screen.getByTestId('campaign-budget')).toHaveTextContent('30m left')
      act(() => { vi.advanceTimersByTime(10 * 60_000) })
      expect(screen.getByTestId('campaign-budget')).toHaveTextContent('20m left')
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('validateExtend', () => {
  const now = new Date('2026-10-26T12:00:00Z').getTime()
  const base = campaignFixture({ status: 'deadline_reached', deadline_at: '2026-10-26T10:00:00Z' })
  const local = (iso: string) => {
    const d = new Date(iso)
    const pad = (n: number) => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
  }
  const initial = local('2026-10-26T10:00:00Z')

  it('needs a later deadline when the deadline ended it', () => {
    expect(validateExtend(base, initial, '100000', initial, '100000', now)).toEqual({ error: 'campaigns.extend.needDeadline' })
    expect(validateExtend(base, initial, '200000', initial, '100000', now)).toEqual({ error: 'campaigns.extend.needDeadline' })
    expect(validateExtend(base, local('2026-10-26T11:00:00Z'), '100000', initial, '100000', now)).toEqual({ error: 'campaigns.error.invalidDeadline' })
    expect(validateExtend(base, local('2026-10-27T11:00:00Z'), '100000', initial, '100000', now))
      .toEqual({ input: { deadline_at: '2026-10-27T11:00:00.000Z' } })
  })

  it('[CMP-01] extends a goal without a token budget by its deadline only', () => {
    const plain = campaignFixture({ ...base, budget: { retro_reserve_ratio: 0.05 }, usable_tokens: null })
    expect(validateExtend(plain, initial, '', initial, '', now)).toEqual({ error: 'campaigns.extend.needDeadline' })
    expect(validateExtend(plain, local('2026-10-27T11:00:00Z'), '', initial, '', now))
      .toEqual({ input: { deadline_at: '2026-10-27T11:00:00.000Z' } })
  })

  it('needs a bigger budget when the budget ended it', () => {
    const spent = campaignFixture({ status: 'budget_exhausted' })
    expect(validateExtend(spent, '', '100000', '', '100000', now)).toEqual({ error: 'campaigns.extend.needBudget' })
    expect(validateExtend(spent, local('2026-10-27T11:00:00Z'), '100000', '', '100000', now)).toEqual({ error: 'campaigns.extend.needBudget' })
    expect(validateExtend(spent, '', '100000x', '', '100000', now)).toEqual({ error: 'campaigns.form.positiveInteger' })
    expect(validateExtend(spent, '', '100001', '', '100000', now)).toEqual({ input: { max_total_tokens: 100001 } })
  })
})

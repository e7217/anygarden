// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import CampaignSection from './CampaignSection'
import { FeedbackProvider } from '@/components/feedback/FeedbackProvider'
import { attemptFixture, campaignFixture } from '@/lib/campaignFixtures'
import type { Campaign } from '@/lib/campaigns'
import type { Room } from '@/hooks/useRooms'

const mocks = vi.hoisted(() => ({
  user: { id: 'user-1', email: 'a@b.c', is_admin: false } as { id: string; email: string; is_admin: boolean } | null,
  campaigns: [] as Campaign[],
  act: vi.fn(async () => {}),
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
    extend: vi.fn(),
  }),
}))

const projectRoom: Room = { id: 'room-1', name: 'r', project_id: 'p', is_dm: false, representative_agent_id: 'agent-1' }

function renderSection(room: Room | null = projectRoom) {
  return render(<FeedbackProvider><CampaignSection roomId="room-1" room={room} /></FeedbackProvider>)
}

beforeEach(() => {
  mocks.user = { id: 'user-1', email: 'a@b.c', is_admin: false }
  mocks.campaigns = []
  mocks.act.mockClear()
})
afterEach(cleanup)

describe('CampaignSection', () => {
  it('stays hidden in a room that cannot host a campaign and has none', () => {
    renderSection({ ...projectRoom, representative_agent_id: null })
    expect(screen.queryByTestId('campaign-section')).toBeNull()
  })

  it('offers to start one in a project room with a lead', () => {
    renderSection()
    expect(screen.getByRole('button', { name: 'New campaign' })).toBeInTheDocument()
    expect(screen.getByText(/No campaigns yet/)).toBeInTheDocument()
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
    expect(dialog).toHaveTextContent('Stop this campaign?')
    expect(mocks.act).not.toHaveBeenCalled()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Stop' }))
    await waitFor(() => expect(mocks.act).toHaveBeenCalledWith('camp-1', 'stop'))
  })

  it('offers extend only once the deadline or budget ended it', () => {
    mocks.campaigns = [campaignFixture({ status: 'budget_exhausted' })]
    renderSection()
    expect(screen.getByRole('button', { name: 'Extend' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Stop' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Extend' }))
    expect(screen.getByLabelText('New token budget')).toHaveValue('100000')
  })
})

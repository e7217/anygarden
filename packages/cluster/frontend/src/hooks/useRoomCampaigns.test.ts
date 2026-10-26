// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { useRoomCampaigns } from './useRoomCampaigns'
import { attemptFixture, campaignFixture } from '@/lib/campaignFixtures'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

const dispatch = (name: string, detail: unknown) =>
  act(() => { window.dispatchEvent(new CustomEvent(name, { detail })) })

beforeEach(() => {
  vi.restoreAllMocks()
  vi.spyOn(globalThis, 'fetch').mockImplementation(async input => {
    const url = String(input)
    if (url === '/api/v1/rooms/room-1/campaigns') return json([campaignFixture()])
    if (url.startsWith('/api/v1/campaigns/camp-1/attempts')) {
      return json({ items: [attemptFixture()], total: 1, next_offset: null })
    }
    if (url === '/api/v1/campaigns/camp-1/pause') return json(campaignFixture({ status: 'paused', state_revision: 2 }))
    return json({}, 404)
  })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('useRoomCampaigns', () => {
  it('loads campaigns with their recent attempts', async () => {
    const { result } = renderHook(() => useRoomCampaigns('room-1'))
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.campaigns.map(c => c.id)).toEqual(['camp-1'])
    expect(result.current.attempts['camp-1'].map(a => a.id)).toEqual(['att-1'])
  })

  it('applies campaign.updated frames for this room and ignores stale or foreign ones', async () => {
    const { result } = renderHook(() => useRoomCampaigns('room-1'))
    await waitFor(() => expect(result.current.loading).toBe(false))

    dispatch('anygarden:campaign:updated', { type: 'campaign.updated', campaign: campaignFixture({ status: 'blocked', state_revision: 3 }) })
    expect(result.current.campaigns[0].status).toBe('blocked')

    // An older revision arriving late does not roll the state back.
    dispatch('anygarden:campaign:updated', { type: 'campaign.updated', campaign: campaignFixture({ status: 'active', state_revision: 2 }) })
    expect(result.current.campaigns[0].status).toBe('blocked')

    dispatch('anygarden:campaign:updated', { type: 'campaign.updated', campaign: campaignFixture({ id: 'other', room_id: 'room-2' }) })
    expect(result.current.campaigns).toHaveLength(1)

    // A new campaign in this room appears first.
    dispatch('anygarden:campaign:updated', { type: 'campaign.updated', campaign: campaignFixture({ id: 'camp-2' }) })
    expect(result.current.campaigns.map(c => c.id)).toEqual(['camp-2', 'camp-1'])
  })

  it('upserts campaign.attempt_updated frames newest first, five at most', async () => {
    const { result } = renderHook(() => useRoomCampaigns('room-1'))
    await waitFor(() => expect(result.current.loading).toBe(false))

    dispatch('anygarden:campaign:attempt_updated', {
      type: 'campaign.attempt_updated', campaign_id: 'camp-1',
      attempt: attemptFixture({ outcome: 'improved', dev_score: 0.8 }),
    })
    expect(result.current.attempts['camp-1']).toHaveLength(1)
    expect(result.current.attempts['camp-1'][0].outcome).toBe('improved')

    for (let no = 2; no <= 7; no++) {
      dispatch('anygarden:campaign:attempt_updated', {
        type: 'campaign.attempt_updated', campaign_id: 'camp-1', attempt: attemptFixture({ id: `att-${no}`, cycle_no: no }),
      })
    }
    expect(result.current.attempts['camp-1'].map(a => a.cycle_no)).toEqual([7, 6, 5, 4, 3])

    // A frame for a campaign not listed yet is kept for when it shows up.
    dispatch('anygarden:campaign:attempt_updated', { type: 'campaign.attempt_updated', campaign_id: 'nope', attempt: attemptFixture({ id: 'att-x' }) })
    expect(result.current.attempts.nope.map(a => a.id)).toEqual(['att-x'])
    expect(result.current.campaigns.map(c => c.id)).toEqual(['camp-1'])
  })

  it('keeps a newer frame that arrived while the list request was in flight', async () => {
    let release!: () => void
    const gate = new Promise<void>(resolve => { release = resolve })
    vi.mocked(globalThis.fetch).mockImplementation(async input => {
      const url = String(input)
      if (url === '/api/v1/rooms/room-1/campaigns') { await gate; return json([campaignFixture({ state_revision: 1 })]) }
      if (url.startsWith('/api/v1/campaigns/camp-1/attempts')) {
        return json({ items: [attemptFixture()], total: 1, next_offset: null })
      }
      return json({}, 404)
    })
    const { result } = renderHook(() => useRoomCampaigns('room-1'))
    dispatch('anygarden:campaign:updated', { type: 'campaign.updated', campaign: campaignFixture({ status: 'paused', state_revision: 2 }) })
    dispatch('anygarden:campaign:attempt_updated', {
      type: 'campaign.attempt_updated', campaign_id: 'camp-1',
      attempt: attemptFixture({ id: 'att-2', cycle_no: 2 }),
    })
    await act(async () => { release() })
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.campaigns).toHaveLength(1)
    expect(result.current.campaigns[0].status).toBe('paused')
    expect(result.current.attempts['camp-1'].map(a => a.id)).toEqual(['att-2', 'att-1'])
  })

  it('reloads after the room socket reconnects', async () => {
    const { result } = renderHook(() => useRoomCampaigns('room-1'))
    await waitFor(() => expect(result.current.loading).toBe(false))
    vi.mocked(globalThis.fetch).mockImplementation(async input => {
      const url = String(input)
      if (url === '/api/v1/rooms/room-1/campaigns') return json([campaignFixture({ status: 'blocked', state_revision: 5 })])
      return json({ items: [], total: 0, next_offset: null })
    })
    dispatch('anygarden:ws:reconnected', { room_id: 'room-2' })
    expect(result.current.campaigns[0].status).toBe('active')
    dispatch('anygarden:ws:reconnected', { room_id: 'room-1' })
    await waitFor(() => expect(result.current.campaigns[0].status).toBe('blocked'))
  })

  it('applies the response of an action', async () => {
    const { result } = renderHook(() => useRoomCampaigns('room-1'))
    await waitFor(() => expect(result.current.loading).toBe(false))
    await act(() => result.current.act('camp-1', 'pause'))
    expect(result.current.campaigns[0].status).toBe('paused')
  })
})

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  createCampaign,
  extendCampaign,
  listCampaignAttempts,
  listRoomCampaigns,
  runCampaignAction,
  type Campaign,
  type CampaignAction,
  type CampaignAttempt,
  type CampaignCreateInput,
  type CampaignExtendInput,
} from '@/lib/campaigns'

/** Attempts the rail shows per campaign. */
export const RECENT_ATTEMPTS = 5

export interface UseRoomCampaignsValue {
  campaigns: Campaign[]
  /** Newest first, at most ``RECENT_ATTEMPTS`` per campaign. */
  attempts: Record<string, CampaignAttempt[]>
  loading: boolean
  error: string | null
  refresh: () => Promise<void>
  create: (input: CampaignCreateInput) => Promise<Campaign>
  act: (campaignId: string, action: CampaignAction) => Promise<void>
  extend: (campaignId: string, input: CampaignExtendInput) => Promise<void>
}

interface State {
  roomId: string | null
  campaigns: Campaign[]
  attempts: Record<string, CampaignAttempt[]>
  loading: boolean
  error: string | null
}

/** Replace *incoming* into *list*, unless the copy held is newer. */
export function upsertCampaign(list: Campaign[], incoming: Campaign): Campaign[] {
  const index = list.findIndex(c => c.id === incoming.id)
  if (index < 0) return [incoming, ...list]
  if (list[index].state_revision > incoming.state_revision) return list
  const next = list.slice()
  next[index] = incoming
  return next
}

export function upsertAttempt(list: CampaignAttempt[], incoming: CampaignAttempt): CampaignAttempt[] {
  return [incoming, ...list.filter(a => a.id !== incoming.id)]
    .sort((a, b) => b.cycle_no - a.cycle_no)
    .slice(0, RECENT_ATTEMPTS)
}

/**
 * The room's campaigns and their recent attempts (#930), kept current by
 * the ``campaign.updated`` / ``campaign.attempt_updated`` frames that
 * ``useWebSocket`` rebroadcasts on ``window``. ``null`` suspends it.
 */
export function useRoomCampaigns(roomId: string | null): UseRoomCampaignsValue {
  const [state, setState] = useState<State>(() => ({
    roomId, campaigns: [], attempts: {}, loading: Boolean(roomId), error: null,
  }))
  const currentRoom = useRef(roomId)
  currentRoom.current = roomId
  const request = useRef(0)

  const refresh = useCallback(async () => {
    if (!roomId) return
    const seq = ++request.current
    const accepts = () => currentRoom.current === roomId && request.current === seq
    setState(prev => ({
      roomId,
      campaigns: prev.roomId === roomId ? prev.campaigns : [],
      attempts: prev.roomId === roomId ? prev.attempts : {},
      loading: true,
      error: null,
    }))
    try {
      const campaigns = await listRoomCampaigns(roomId)
      const pages = await Promise.all(
        campaigns.map(c => listCampaignAttempts(c.id, RECENT_ATTEMPTS).then(p => p.items).catch(() => [])),
      )
      if (!accepts()) return
      const attempts: Record<string, CampaignAttempt[]> = {}
      campaigns.forEach((c, i) => { attempts[c.id] = pages[i] })
      setState({ roomId, campaigns, attempts, loading: false, error: null })
    } catch (error) {
      if (!accepts()) return
      setState({ roomId, campaigns: [], attempts: {}, loading: false, error: error instanceof Error ? error.message : String(error) })
    }
  }, [roomId])

  useEffect(() => { void refresh() }, [refresh])

  useEffect(() => {
    if (!roomId) return
    const onCampaign = (event: Event) => {
      const campaign = (event as CustomEvent<{ campaign?: Campaign }>).detail?.campaign
      if (!campaign || campaign.room_id !== roomId) return
      setState(prev => prev.roomId !== roomId ? prev : { ...prev, campaigns: upsertCampaign(prev.campaigns, campaign) })
    }
    const onAttempt = (event: Event) => {
      const detail = (event as CustomEvent<{ campaign_id?: string; attempt?: CampaignAttempt }>).detail
      const id = detail?.campaign_id
      const attempt = detail?.attempt
      if (!id || !attempt) return
      setState(prev => {
        if (prev.roomId !== roomId || !prev.campaigns.some(c => c.id === id)) return prev
        return { ...prev, attempts: { ...prev.attempts, [id]: upsertAttempt(prev.attempts[id] ?? [], attempt) } }
      })
    }
    window.addEventListener('anygarden:campaign:updated', onCampaign)
    window.addEventListener('anygarden:campaign:attempt_updated', onAttempt)
    return () => {
      window.removeEventListener('anygarden:campaign:updated', onCampaign)
      window.removeEventListener('anygarden:campaign:attempt_updated', onAttempt)
    }
  }, [roomId])

  const apply = useCallback((campaign: Campaign) => {
    if (currentRoom.current !== campaign.room_id) return
    setState(prev => prev.roomId !== campaign.room_id ? prev : {
      ...prev,
      campaigns: upsertCampaign(prev.campaigns, campaign),
      attempts: prev.attempts[campaign.id] ? prev.attempts : { ...prev.attempts, [campaign.id]: [] },
    })
  }, [])

  const create = useCallback(async (input: CampaignCreateInput) => {
    if (!roomId) throw new Error('No room')
    const campaign = await createCampaign(roomId, input)
    apply(campaign)
    return campaign
  }, [roomId, apply])
  const act = useCallback(async (id: string, action: CampaignAction) => {
    apply(await runCampaignAction(id, action))
  }, [apply])
  const extend = useCallback(async (id: string, input: CampaignExtendInput) => {
    apply(await extendCampaign(id, input))
  }, [apply])

  const current = state.roomId === roomId && roomId
    ? state
    : { campaigns: [], attempts: {}, loading: Boolean(roomId), error: null }
  return {
    campaigns: current.campaigns,
    attempts: current.attempts,
    loading: current.loading,
    error: current.error,
    refresh,
    create,
    act,
    extend,
  }
}

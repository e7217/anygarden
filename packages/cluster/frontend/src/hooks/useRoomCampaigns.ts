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
 * Fold the copies already held (*held*, e.g. from WebSocket frames that
 * arrived while the list request was in flight) into a fresh REST *list*:
 * the higher ``state_revision`` wins, and a campaign the list does not have
 * yet (created after its snapshot) stays on top.
 */
export function mergeCampaigns(list: Campaign[], held: Campaign[]): Campaign[] {
  return held.reduceRight((acc, campaign) => upsertCampaign(acc, campaign), list)
}

function attemptProgress(a: CampaignAttempt): number {
  return (a.trusted_score != null ? 8 : 0) + (a.outcome ? 4 : 0) + (a.closed_at ? 2 : 0) + (a.eval_status !== 'pending' ? 1 : 0)
}

/** Union of fetched and held attempts per campaign, newest first, capped. */
export function mergeAttempts(
  fetched: Record<string, CampaignAttempt[]>,
  held: Record<string, CampaignAttempt[]>,
): Record<string, CampaignAttempt[]> {
  const out: Record<string, CampaignAttempt[]> = { ...held }
  for (const [id, items] of Object.entries(fetched)) {
    const byId = new Map<string, CampaignAttempt>()
    for (const a of items) byId.set(a.id, a)
    for (const a of held[id] ?? []) {
      const other = byId.get(a.id)
      // Attempts carry no revision: keep the copy that got further.
      if (!other || attemptProgress(a) > attemptProgress(other)) byId.set(a.id, a)
    }
    out[id] = [...byId.values()].sort((a, b) => b.cycle_no - a.cycle_no).slice(0, RECENT_ATTEMPTS)
  }
  return out
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
      const fetched: Record<string, CampaignAttempt[]> = {}
      campaigns.forEach((c, i) => { fetched[c.id] = pages[i] })
      setState(prev => {
        const held = prev.roomId === roomId
        return {
          roomId,
          campaigns: held ? mergeCampaigns(campaigns, prev.campaigns) : campaigns,
          attempts: held ? mergeAttempts(fetched, prev.attempts) : fetched,
          loading: false,
          error: null,
        }
      })
    } catch (error) {
      if (!accepts()) return
      // Keep what is already shown (e.g. a refetch after a reconnect failed).
      setState(prev => ({
        roomId,
        campaigns: prev.roomId === roomId ? prev.campaigns : [],
        attempts: prev.roomId === roomId ? prev.attempts : {},
        loading: false,
        error: error instanceof Error ? error.message : String(error),
      }))
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
        // Kept even before its campaign is known (the frame may beat the
        // list response); only attempts of listed campaigns are shown.
        if (prev.roomId !== roomId) return prev
        return { ...prev, attempts: { ...prev.attempts, [id]: upsertAttempt(prev.attempts[id] ?? [], attempt) } }
      })
    }
    // Frames sent while the socket was down are lost: reload on reconnect.
    const onReconnect = (event: Event) => {
      if ((event as CustomEvent<{ room_id?: string }>).detail?.room_id === roomId) void refresh()
    }
    window.addEventListener('anygarden:campaign:updated', onCampaign)
    window.addEventListener('anygarden:campaign:attempt_updated', onAttempt)
    window.addEventListener('anygarden:ws:reconnected', onReconnect)
    return () => {
      window.removeEventListener('anygarden:campaign:updated', onCampaign)
      window.removeEventListener('anygarden:campaign:attempt_updated', onAttempt)
      window.removeEventListener('anygarden:ws:reconnected', onReconnect)
    }
  }, [roomId, refresh])

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

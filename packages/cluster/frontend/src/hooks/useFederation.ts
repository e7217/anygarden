// @vitest-environment jsdom
/**
 * useFederation (#593 / task #33) — live state over the real federation
 * endpoints. Replaces the fixture state machine of the Phase 0 mock.
 *
 * Transport is HTTP polling (v1 decision): snapshot cursor ``after_seq`` for
 * channel state, periodic refresh for node/peer state. There is no WS push
 * for federation events, so nothing here holds sockets open.
 *
 * Channel discovery is not exposed by the backend yet (task #34 will add a
 * bindings listing), so the selected channel — plus the follower-side
 * command fields (sender node id, grant epoch) — is entered once and kept in
 * localStorage. Those fields disappear from the UI when #34/#35 land.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { useAuth } from '@/hooks/useAuth'
import {
  FederationApiError,
  type CommandEnvelope,
  type InviteBundleView,
  type InviteView,
  type ParticipantView,
  type PeerView,
  type Principal,
  type SnapshotView,
  type SubmissionView,
  acceptInvite,
  changeParticipant as apiChangeParticipant,
  createBinding,
  createInvite,
  getSnapshot,
  getSubmission,
  listInvites,
  listPeers,
  revokeGrant,
  revokeInvite,
  revokePeer,
  setPublication as apiSetPublication,
  submitCommand,
  syncChannel,
  uuid,
} from '@/lib/federationApi'

const STORAGE_KEY = 'anygarden.federation.channel'
const POLL_MS = 5000

export interface FederationChannelRef {
  authority: string
  channel: string
  /** Follower-side command routing (until #34 exposes discovery). */
  senderNodeId?: string
  grantEpoch?: number
}

interface StoredRef extends FederationChannelRef {}

function loadRef(): FederationChannelRef | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as StoredRef
    if (typeof parsed.authority === 'string' && typeof parsed.channel === 'string') {
      return parsed
    }
  } catch {
    // Corrupt storage behaves like “not set”.
  }
  return null
}

function saveRef(ref: FederationChannelRef | null): void {
  if (ref === null) localStorage.removeItem(STORAGE_KEY)
  else localStorage.setItem(STORAGE_KEY, JSON.stringify(ref))
}

export type NodeCapability =
  | 'unknown'
  | 'disabled'
  | 'ready'

export interface TrackedSubmission extends SubmissionView {
  authority: string
  channel: string
  kind: string
  createdAt: number
}

/**
 * Derive node capability from a failing peers call. The product server does
 * not wire the federation services yet (#34): 404 means the admin router is
 * not mounted, 503 SHARING_DISABLED means the channel service is absent.
 * Both render an honest “sharing disabled” surface instead of mock data.
 */
export function capabilityFromError(error: unknown): NodeCapability {
  if (error instanceof FederationApiError) {
    if (error.status === 404 || error.code === 'SHARING_DISABLED') return 'disabled'
  }
  return 'unknown'
}

export function useFederation() {
  const { user } = useAuth()
  const [invites, setInvites] = useState<InviteView[]>([])
  const [peers, setPeers] = useState<PeerView[]>([])
  const [nodesError, setNodesError] = useState<FederationApiError | null>(null)
  const [nodesCapability, setNodesCapability] = useState<NodeCapability>('unknown')
  const [channelRef, setChannelRef] = useState<FederationChannelRef | null>(() => loadRef())
  const [snapshot, setSnapshot] = useState<SnapshotView | null>(null)
  const [snapshotError, setSnapshotError] = useState<FederationApiError | null>(null)
  const [submissions, setSubmissions] = useState<Record<string, TrackedSubmission>>({})
  const [actionError, setActionError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null)

  const isAdmin = user?.is_admin === true

  const refreshNodes = useCallback(async () => {
    try {
      const [nextInvites, nextPeers] = await Promise.all([listInvites(), listPeers()])
      setInvites(nextInvites)
      setPeers(nextPeers)
      setNodesError(null)
      setNodesCapability('ready')
    } catch (error) {
      if (error instanceof FederationApiError) {
        setNodesError(error)
        setNodesCapability(capabilityFromError(error))
      } else {
        setNodesError(null)
        setNodesCapability('unknown')
      }
    }
  }, [])

  const refreshChannel = useCallback(async (ref: FederationChannelRef | null) => {
    if (!ref) {
      setSnapshot(null)
      setSnapshotError(null)
      return
    }
    try {
      // Full-window read: the server caps ``limit`` at 100, and the roster
      // must stay whole so revision ordering never depends on pagination.
      const view = await getSnapshot(ref.authority, ref.channel, 0, 100)
      setSnapshot(view)
      setSnapshotError(null)
    } catch (error) {
      if (error instanceof FederationApiError) setSnapshotError(error)
    }
  }, [])

  const refreshSubmissions = useCallback(async (ref: FederationChannelRef | null) => {
    if (!ref) return
    const entries = Object.values(submissions).filter(
      (item) => item.state === 'unconfirmed' && item.authority === ref.authority,
    )
    if (entries.length === 0) return
    const updates = await Promise.all(
      entries.map(async (item) => {
        try {
          return await getSubmission(ref.authority, ref.channel, item.request_id)
        } catch {
          return null
        }
      }),
    )
    setSubmissions((current) => {
      const next = { ...current }
      entries.forEach((item, index) => {
        const update = updates[index]
        if (update) next[item.request_id] = { ...item, ...update }
      })
      return next
    })
  }, [submissions])

  useEffect(() => {
    if (!isAdmin) return
    void refreshNodes()
  }, [isAdmin, refreshNodes])

  useEffect(() => {
    saveRef(channelRef)
    setSubmissions({})
    void refreshChannel(channelRef)
  }, [channelRef, refreshChannel])

  useEffect(() => {
    if (!isAdmin) return
    const tick = () => {
      if (document.visibilityState !== 'visible') return
      void refreshChannel(channelRef)
      void refreshSubmissions(channelRef)
    }
    pollTimer.current = setInterval(tick, POLL_MS)
    return () => {
      if (pollTimer.current) clearInterval(pollTimer.current)
    }
  }, [isAdmin, channelRef, refreshChannel, refreshSubmissions])

  /** Actor principal derived from the logged-in admin session. */
  const actorPrincipal = useMemo<Principal | null>(() => {
    if (!user || !channelRef?.senderNodeId) return null
    return {
      node_id: channelRef.senderNodeId,
      kind: 'human',
      principal_id: user.id,
    }
  }, [user, channelRef])

  async function run<T>(action: () => Promise<T>): Promise<T | null> {
    setBusy(true)
    setActionError(null)
    try {
      return await action()
    } catch (error) {
      setActionError(
        error instanceof FederationApiError
          ? `${error.code} (${error.status})`
          : 'REQUEST_FAILED',
      )
      return null
    } finally {
      setBusy(false)
    }
  }

  const createInviteAction = useCallback(
    (input: Parameters<typeof createInvite>[0]) =>
      run(async () => {
        const bundle = await createInvite(input)
        await refreshNodes()
        return bundle as InviteBundleView
      }),
    [refreshNodes],
  )

  const acceptInviteAction = useCallback(
    (input: Parameters<typeof acceptInvite>[0]) =>
      run(async () => {
        const result = await acceptInvite(input)
        await refreshNodes()
        return result
      }),
    [refreshNodes],
  )

  const revokeInviteAction = useCallback(
    (inviteId: string) =>
      run(async () => {
        await revokeInvite(inviteId)
        await refreshNodes()
      }),
    [refreshNodes],
  )

  const revokePeerAction = useCallback(
    (nodeId: string) =>
      run(async () => {
        await revokePeer(nodeId)
        await refreshNodes()
      }),
    [refreshNodes],
  )

  const revokeGrantAction = useCallback(
    (nodeId: string, channelId: string) =>
      run(async () => {
        await revokeGrant(nodeId, channelId)
        await refreshNodes()
      }),
    [refreshNodes],
  )

  const bindChannelAction = useCallback(
    (input: Parameters<typeof createBinding>[0]) =>
      run(async () => {
        const binding = await createBinding(input)
        setChannelRef({
          authority: binding.authority_node_id,
          channel: binding.channel_id,
          senderNodeId: channelRef?.senderNodeId,
          grantEpoch: channelRef?.grantEpoch,
        })
        return binding
      }),
    [channelRef],
  )

  const setPublicationAction = useCallback(
    (principal: Principal, active: boolean) =>
      run(async () => {
        if (!channelRef) return
        await apiSetPublication(channelRef.channel, principal, active)
        await refreshChannel(channelRef)
      }),
    [channelRef, refreshChannel],
  )

  const changeParticipantAction = useCallback(
    (principal: Principal, active: boolean, role: string, expectedRevision: number) =>
      run(async () => {
        if (!channelRef) return
        await apiChangeParticipant({
          channelId: channelRef.channel,
          principal,
          active,
          role,
          expected_revision: expectedRevision,
        })
        await refreshChannel(channelRef)
      }),
    [channelRef, refreshChannel],
  )

  const syncAction = useCallback(
    () =>
      run(async () => {
        if (!channelRef?.senderNodeId || !channelRef.grantEpoch || !actorPrincipal) return
        await syncChannel({
          sender_node_id: channelRef.senderNodeId,
          authority_node_id: channelRef.authority,
          channel_id: channelRef.channel,
          grant_epoch: channelRef.grantEpoch,
          actor: actorPrincipal,
        })
        await refreshChannel(channelRef)
        await refreshSubmissions(channelRef)
      }),
    [channelRef, actorPrincipal, refreshChannel, refreshSubmissions],
  )

  function commandEnvelope(kind: string, payload: Record<string, unknown>): CommandEnvelope | null {
    if (!channelRef?.senderNodeId || !channelRef.grantEpoch || !actorPrincipal) return null
    return {
      protocol_version: 1,
      request_id: uuid(),
      sender_node_id: channelRef.senderNodeId,
      authority_node_id: channelRef.authority,
      channel_id: channelRef.channel,
      grant_epoch: channelRef.grantEpoch,
      actor: actorPrincipal,
      kind,
      payload,
    }
  }

  const requestTaskAction = useCallback(
    (input: {
      delegationId: string
      taskId: string
      sourceMessageId: string
      executorNodeId: string
      executorAgentId: string
    }) =>
      run(async () => {
        const envelope = commandEnvelope('task.request', {
          delegation_id: input.delegationId,
          expected_revision: 0,
          task_id: input.taskId,
          source_message_id: input.sourceMessageId,
          executor: { node_id: input.executorNodeId, agent_id: input.executorAgentId },
        })
        if (!envelope || !channelRef) return
        const submission = await submitCommand(envelope)
        setSubmissions((current) => ({
          ...current,
          [envelope.request_id]: {
            ...submission,
            authority: channelRef.authority,
            channel: channelRef.channel,
            kind: envelope.kind,
            createdAt: Date.now(),
          },
        }))
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [channelRef],
  )

  const cancelTaskAction = useCallback(
    (input: { delegationId: string; expectedRevision: number }) =>
      run(async () => {
        const envelope = commandEnvelope('task.cancel', {
          delegation_id: input.delegationId,
          expected_revision: input.expectedRevision,
        })
        if (!envelope || !channelRef) return
        const submission = await submitCommand(envelope)
        setSubmissions((current) => ({
          ...current,
          [envelope.request_id]: {
            ...submission,
            authority: channelRef.authority,
            channel: channelRef.channel,
            kind: envelope.kind,
            createdAt: Date.now(),
          },
        }))
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [channelRef],
  )

  const roster: ParticipantView[] = useMemo(() => {
    if (!snapshot) return []
    return [...snapshot.participants].sort((a, b) =>
      a.principal.node_id === b.principal.node_id
        ? a.principal.principal_id.localeCompare(b.principal.principal_id)
        : a.principal.node_id.localeCompare(b.principal.node_id),
    )
  }, [snapshot])

  return {
    isAdmin,
    invites,
    peers,
    nodesError,
    nodesCapability,
    refreshNodes,
    channelRef,
    setChannelRef,
    snapshot,
    snapshotError,
    roster,
    submissions: Object.values(submissions).sort((a, b) => b.createdAt - a.createdAt),
    actorPrincipal,
    busy,
    actionError,
    createInvite: createInviteAction,
    acceptInvite: acceptInviteAction,
    revokeInvite: revokeInviteAction,
    revokePeer: revokePeerAction,
    revokeGrant: revokeGrantAction,
    bindChannel: bindChannelAction,
    setPublication: setPublicationAction,
    changeParticipant: changeParticipantAction,
    sync: syncAction,
    requestTask: requestTaskAction,
    cancelTask: cancelTaskAction,
  }
}

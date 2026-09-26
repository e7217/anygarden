import { useState, useMemo, useEffect, useCallback } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import Sidebar from '@/components/Sidebar'
import SidebarExpandButton from '@/components/SidebarExpandButton'
import RoomHeader from '@/components/RoomHeader'
import ChatArea from '@/components/ChatArea'
import MessageInput from '@/components/MessageInput'
import RoomArtifactsDialog from '@/components/RoomArtifactsDialog'
import RoomActivityDialog from '@/components/RoomActivityDialog'
import TypingIndicator from '@/components/TypingIndicator'
import ManageRoomAgentsDialog from '@/components/ManageRoomAgentsDialog'
import CreateSubRoomDialog from '@/components/CreateSubRoomDialog'
import RoomEditDialog from '@/components/RoomEditDialog'
import RoomInviteDialog from '@/components/RoomInviteDialog'
import RoomWorkspaceDialog from '@/components/RoomWorkspaceDialog'
import ParticipantListPopover from '@/components/ParticipantListPopover'
import SearchDialog from '@/components/SearchDialog'
import RightContextRail from '@/components/RightContextRail'
import ThreadPanel from '@/components/ThreadPanel'
import ThreadInline from '@/components/ThreadInline'
import { indexThreads, canHostThread } from '@/lib/threads'
import { clearDraft, threadDraftKey } from '@/lib/composerDrafts'
import { useThreadDisplayMode } from '@/hooks/useThreadDisplayMode'
import WorkspaceAttachmentBanner from '@/components/WorkspaceAttachmentBanner'
import RightRailToggle from '@/components/right-rail/RightRailToggle'
import { Button } from '@/components/ui/button'
import { useWebSocket } from '@/hooks/useWebSocket'
import { useParticipantPresence } from '@/hooks/useParticipantPresence'
import { useRooms, type Room } from '@/hooks/useRooms'
import { useAuth } from '@/hooks/useAuth'
import { apiFetch } from '@/lib/api'
import { MessageSquare, Menu, Plus } from 'lucide-react'
import type { MentionOption } from '@/components/MentionPopover'
import { useLocale } from '@/i18n/LocaleProvider'
import { useFeedback } from '@/components/feedback/FeedbackProvider'

export interface Participant {
  id: string
  display_name: string
  kind: string
  user_id?: string
  agent_id?: string
  // Mirrors ``ParticipantOut.role`` from ``rooms/router.py``.
  // Used for per-room admin-ish UI gating (e.g. the Invites button)
  // — the server remains the sole authority.
  role?: string
  // True for anonymous guest users. Lets the UI show a distinct
  // "guest" badge without having to introduce a new ``kind`` value,
  // which would break legacy callers expecting ``user``/``agent``.
  is_anonymous?: boolean
  // Presence fields (#54). Populated from ``GET /rooms/{id}`` and
  // merged in realtime via ``useParticipantPresence`` WS patches.
  online?: boolean
  last_seen_at?: string | null
  // Agent engine identifier (#102). Populated when ``kind === 'agent'``
  // from the backing ``Agent.engine`` row; undefined for user/guest.
  // Drives the engine-mark badge on ``EntityAvatar`` (available to
  // non-admin viewers too, unlike the admin-gated ``useAgents()``).
  engine?: string
  // Issue #101 — agent avatar override (null for user participants).
  avatar_kind?: string | null
  avatar_value?: string | null
  // Issue #271 — short public-facing self-introduction. Populated for
  // agent participants whose admin set ``Agent.description``; null
  // for users/guests and for agents without a description set.
  description?: string | null
}

export default function ChatPage() {
  const { t } = useLocale()
  const { confirm, notify } = useFeedback()
  const { roomId } = useParams<{ roomId: string }>()
  const navigate = useNavigate()
  const selectedRoom = roomId ?? null
  const {
    projects,
    rooms,
    agentDMs,
    fetchRooms,
    fetchAgentDMs,
    setRoomEphemeral,
    markRoomRead,
  } = useRooms()
  const { user } = useAuth()
  const { messages, connected, typingUsers, typingStages, send, sendTyping } = useWebSocket(selectedRoom)
  const [participants, setParticipants] = useState<Record<string, Participant>>({})
  const [myParticipantId, setMyParticipantId] = useState<string | null>(null)
  // Thread grouping is derived once here so the timeline and the side
  // panel can never disagree about which messages are replies.
  const threadIndex = useMemo(() => indexThreads(messages), [messages])
  const [threadRootId, setThreadRootId] = useState<string | null>(null)
  const [threadMode, setThreadMode] = useThreadDisplayMode()
  // An orphaned reply is in ``roots`` so it stays visible, but it can
  // never host a thread — the server rejects a thread rooted at a reply.
  // ChatArea already hides the affordance; this second guard keeps the
  // panel from opening one through any other path.
  const threadRoot = useMemo(
    () =>
      threadRootId && canHostThread(threadIndex, threadRootId)
        ? threadIndex.roots.find(m => m.id === threadRootId) ?? null
        : null,
    [threadRootId, threadIndex],
  )
  // Closing returns focus to the control that opened the thread.
  // Without this the caret lands back at the top of the document and a
  // keyboard user has to walk the whole timeline again to reach the
  // message they were just reading. The composer's draft is dropped —
  // an explicitly closed thread is a discarded one; a *layout switch*
  // keeps it, which is the case the draft store exists for.
  const closeThread = useCallback(() => {
    const id = threadRootId
    setThreadRootId(null)
    if (!id) return
    clearDraft(threadDraftKey(id))
    // Rendered by ChatArea, so query rather than thread a ref through
    // the timeline for a control that may not exist after a reflow.
    requestAnimationFrame(() => {
      const trigger = document.querySelector<HTMLElement>(
        `[data-thread-trigger="${id}"]`,
      )
      trigger?.focus()
    })
  }, [threadRootId])

  // Close the panel when the room changes, and when the open root
  // leaves the loaded window (history is capped, so a long-lived
  // session can scroll it out). Without the second guard the panel
  // would stay mounted with nothing to show.
  useEffect(() => {
    setThreadRootId(null)
  }, [selectedRoom])
  useEffect(() => {
    if (threadRootId && !threadRoot) setThreadRootId(null)
  }, [threadRootId, threadRoot])
  const [agentDialogOpen, setAgentDialogOpen] = useState(false)
  const [subRoomDialogOpen, setSubRoomDialogOpen] = useState(false)
  const [artifactsOpen, setArtifactsOpen] = useState(false)
  const [roomActivityOpen, setRoomActivityOpen] = useState(false)
  const [roomEditOpen, setRoomEditOpen] = useState(false)
  const [roomInvitesOpen, setRoomInvitesOpen] = useState(false)
  const [workspaceRoomId, setWorkspaceRoomId] = useState<string | null>(null)
  const [participantsOpen, setParticipantsOpen] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [createProjectRequest, requestCreateProject] = useState(0)
  const [createRoomRequest, requestCreateRoom] = useState(0)
  // #115 — desktop collapse state + Ctrl/Cmd+B handler now live in
  // <Sidebar> + <SidebarExpandButton>, backed by <SidebarLayoutProvider>.
  // ChatPage no longer owns any sidebar-collapse state.
  const [searchOpen, setSearchOpen] = useState(false)
  // #302 — right context rail mobile drawer state. Desktop collapse
  // is owned by <RightSidebarLayoutProvider> (see App.tsx). The mobile
  // overlay (<md viewport) is local to ChatPage because only the chat
  // route hosts the rail right now.
  const [rightRailOpen, setRightRailOpen] = useState(false)
  const [participantsVersion, setParticipantsVersion] = useState(0)

  const currentRoom = useMemo<Room | null>(() => {
    if (!selectedRoom) return null
    for (const projectRooms of Object.values(rooms)) {
      const found = projectRooms.find(r => r.id === selectedRoom)
      if (found) return found
    }
    // Also search agent DM rooms
    const dm = agentDMs.find(r => r.id === selectedRoom)
    if (dm) return dm
    return null
  }, [selectedRoom, rooms, agentDMs])

  // #116 — 1:1 agent DMs hide controls that are meaningless (agents
  // 1/1 badge, representative select with only one choice, participant
  // toggle, sub-room / edit room / manage-agents) or that break the
  // DM invariant (invite links). The guard lives here at the call
  // site because RoomHeader / RoomSettingsMenu already treat an
  // undefined handler/value as "hide", so we just flip the relevant
  // props to undefined when ``isDm`` is true. Uses ``!!`` so that a
  // null currentRoom (waiting for the room details fetch) never
  // accidentally flips the guard true.
  const isDm = !!currentRoom?.is_dm

  // Walk the ``parent_room_id`` chain upward and return the parent
  // breadcrumb (root → direct parent). Rooms from the same
  // project always share the same ``rooms[project_id]`` array, so
  // a single lookup table per project is enough. If the chain hits
  // a missing link (parent was deleted + cascade-detached, or the
  // user doesn't have access) we stop early and return what we
  // have — the header UI treats an empty breadcrumb as "no parent".
  const parentBreadcrumb = useMemo(() => {
    if (!currentRoom || !currentRoom.parent_room_id) return []
    const byId = new Map<string, Room>()
    // #179 — DM rooms live outside any project (``project_id === null``).
    // Their parent chain, if any, cannot be resolved through the
    // project-scoped ``rooms`` map — fall through to "no breadcrumb"
    // rather than crashing on a null index.
    if (currentRoom.project_id) {
      for (const r of (rooms[currentRoom.project_id] ?? [])) byId.set(r.id, r)
    }
    const chain: { id: string; name: string }[] = []
    let cursor: string | null | undefined = currentRoom.parent_room_id
    // Cap the walk at 32 levels as a cycle-safety belt — the
    // server also enforces a self-reference check at create time.
    let hops = 0
    while (cursor && hops < 32) {
      const node = byId.get(cursor)
      if (!node) break
      chain.unshift({ id: node.id, name: node.name })
      cursor = node.parent_room_id ?? null
      hops += 1
    }
    return chain
  }, [currentRoom, rooms])

  // Ctrl+K → open search
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        setSearchOpen(prev => !prev)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  // #115 — Ctrl/Cmd+B now lives inside <Sidebar>'s useEffect so the
  // shortcut binds only on routes that render Sidebar.

  useEffect(() => {
    if (!selectedRoom) return
    void markRoomRead(selectedRoom)
  }, [selectedRoom, markRoomRead])

  useEffect(() => {
    if (!selectedRoom || messages.length === 0) return
    const timer = window.setTimeout(() => {
      void markRoomRead(selectedRoom)
    }, 1500)
    return () => window.clearTimeout(timer)
  }, [selectedRoom, messages.length, markRoomRead])

  // Fetch room details to get participants with display_name/kind
  useEffect(() => {
    if (!selectedRoom) {
      setParticipants({})
      setMyParticipantId(null)
      return
    }
    let cancelled = false
    ;(async () => {
      try {
        const resp = await apiFetch(`/api/v1/rooms/${selectedRoom}`)
        if (!resp.ok || cancelled) return
        const room = await resp.json()
        const pMap: Record<string, Participant> = {}
        let myPid: string | null = null
        for (const p of room.participants ?? []) {
          pMap[p.id] = {
            id: p.id,
            display_name: p.display_name ?? p.name ?? p.id.slice(0, 8),
            kind: p.kind ?? 'user',
            user_id: p.user_id,
            agent_id: p.agent_id,
            role: p.role,
            is_anonymous: Boolean(p.is_anonymous),
            online: typeof p.online === 'boolean' ? p.online : false,
            last_seen_at: p.last_seen_at ?? null,
            // #102 — thread agent engine through so MessageBubble can
            // render the engine badge without admin access.
            engine: p.engine,
            // Issue #101 — avatar override per agent participant.
            avatar_kind: p.avatar_kind ?? null,
            avatar_value: p.avatar_value ?? null,
          }
          if (user && p.user_id === user.id) {
            myPid = p.id
          }
        }
        if (!cancelled) {
          setParticipants(pMap)
          setMyParticipantId(myPid)
        }
      } catch { /* ignore */ }
    })()
    return () => { cancelled = true }
  }, [selectedRoom, user, participantsVersion])

  const refreshParticipants = useCallback(() => {
    setParticipantsVersion(v => v + 1)
  }, [])

  // Agent IDs that are participants in the current room (for the manage dialog)
  const participantAgentIds = useMemo(() => {
    const ids = new Set<string>()
    for (const p of Object.values(participants)) {
      if (p.agent_id) ids.add(p.agent_id)
    }
    return ids
  }, [participants])

  // #179 — DMs have ``project_id === null``; collapse null→undefined so
  // downstream dialogs keyed on ``string | undefined`` (SearchDialog)
  // treat a DM room the same as "no project scope".
  const currentProjectId = currentRoom?.project_id ?? undefined

  const mentionUsers = useMemo<MentionOption[]>(
    () => Object.values(participants).map(p => ({
      id: p.id,
      display: p.display_name,
      kind: (p.kind === 'agent' ? 'agent' : 'user') as 'user' | 'agent',
      // #271 — propagate the agent's public introduction so the
      // autocomplete can render a secondary line.
      description: p.description ?? null,
    })),
    [participants],
  )

  // #54 — realtime presence. REST ``/rooms/{id}`` seeds the initial
  // map; WS ``presence_update`` frames keep it current. Downstream
  // consumers (agent list, popover, header) read through this so
  // they stay in sync without re-fetching.
  const presenceSeed = useMemo(
    () =>
      Object.values(participants).map(p => ({
        id: p.id,
        online: p.online,
        last_seen_at: p.last_seen_at,
      })),
    [participants],
  )
  const presence = useParticipantPresence(selectedRoom, presenceSeed)

  const agentParticipants = useMemo(
    () => Object.values(participants)
      .filter(p => p.kind === 'agent' && p.agent_id)
      .map(p => ({
        id: p.id,
        agent_id: p.agent_id!,
        display_name: p.display_name,
        online: presence[p.id]?.online ?? Boolean(p.online),
      })),
    [participants, presence],
  )

  // Aggregate "agents N online / M total" for the header badge (#54).
  const { agentsOnline, agentsTotal } = useMemo(() => {
    let total = 0
    let on = 0
    for (const p of Object.values(participants)) {
      if (p.kind !== 'agent') continue
      total += 1
      if (presence[p.id]?.online ?? Boolean(p.online)) on += 1
    }
    return { agentsOnline: on, agentsTotal: total }
  }, [participants, presence])

  // For DM rooms, expose the partner agent so RoomHeader can swap
  // its left Hash glyph for an engine-colored avatar. Derived from
  // the participants map (not the admin-gated ``useAgents`` hook)
  // so non-admin viewers of a DM still get the avatar. Engine stays
  // unknown until we thread it through the participant payload —
  // EntityAvatar gracefully omits the corner badge in that case.
  const dmAgent = useMemo(() => {
    const agentP = Object.values(participants).find(
      (p) => p.kind === 'agent',
    )
    if (!agentP) return undefined
    return {
      id: agentP.id,
      name: agentP.display_name,
      engine: agentP.engine,
      // Issue #101 — propagate the avatar so the RoomHeader of a
      // DM shows the same custom glyph the admin picked.
      avatar_kind: agentP.avatar_kind ?? null,
      avatar_value: agentP.avatar_value ?? null,
    }
  }, [participants])

  // Mirror the server auth rule (api/v1/invites.py::_require_room_admin_or_owner):
  // global admin OR a room-level admin/owner Participant. The server
  // stays the sole authority; this flag only controls whether the UI
  // bothers rendering the X button.
  const canRemoveParticipants = useMemo(() => {
    if (user?.is_admin) return true
    const myRole = myParticipantId ? participants[myParticipantId]?.role : undefined
    return myRole === 'admin' || myRole === 'owner'
  }, [user, myParticipantId, participants])

  const handleRemoveParticipant = useCallback(
    async (participantId: string) => {
      if (!selectedRoom) return
      try {
        const resp = await apiFetch(
          `/api/v1/rooms/${selectedRoom}/participants/${participantId}`,
          { method: 'DELETE' },
        )
        if (resp.status === 204) {
          refreshParticipants()
          return
        }
        let detail = t('chat.failedRemoveParticipant', { status: resp.status })
        try {
          const body = await resp.json()
          if (body && typeof body.detail === 'string') detail = body.detail
        } catch { /* ignore body parse */ }
        notify({ message: detail, tone: 'error' })
      } catch (err) {
        notify({ message: err instanceof Error ? err.message : String(err), tone: 'error' })
      }
    },
    [selectedRoom, refreshParticipants, notify, t],
  )

  const handleDeleteRoom = useCallback(async () => {
    if (!selectedRoom || !currentRoom) return
    const ok = await confirm({
      title: t('chat.deleteRoomTitle', { name: currentRoom.name }),
      description: t('chat.deleteRoomDescription'),
      confirmLabel: t('chat.delete'),
      destructive: true,
    })
    if (!ok) return
    try {
      const resp = await apiFetch(`/api/v1/rooms/${selectedRoom}`, {
        method: 'DELETE',
      })
      if (resp.status === 204) {
        // The server's broadcast also tells us via WS, but acting
        // immediately on the success response avoids depending on
        // the round-trip — keeps the UI snappy. Navigate away
        // first; the room-deleted event listener will repeat the
        // navigation harmlessly when the WS frame eventually lands.
        navigate('/')
        return
      }
      let detail = t('chat.failedDeleteRoom', { status: resp.status })
      try {
        const body = await resp.json()
        if (body && typeof body.detail === 'string') detail = body.detail
      } catch { /* ignore body parse */ }
      notify({ message: detail, tone: 'error' })
    } catch (err) {
      notify({ message: err instanceof Error ? err.message : String(err), tone: 'error' })
    }
  }, [selectedRoom, currentRoom, navigate, t, confirm, notify])

  // Listen for ``room_deleted`` WS frames pushed for OTHER sessions
  // — e.g. another tab the same user has open, or a host deleted a
  // room while we were in it. Without this we'd silently keep
  // showing the deleted room's stale content.
  useEffect(() => {
    const onDeleted = (e: Event) => {
      const detail = (e as CustomEvent).detail as { room_id?: string } | undefined
      if (detail?.room_id && detail.room_id === selectedRoom) {
        navigate('/')
      }
    }
    window.addEventListener('anygarden:room:deleted', onDeleted)
    return () => window.removeEventListener('anygarden:room:deleted', onDeleted)
  }, [selectedRoom, navigate])

  const handleSetRepresentative = useCallback(async (agentId: string | null) => {
    if (!selectedRoom) return
    await apiFetch(`/api/v1/rooms/${selectedRoom}/representative`, {
      method: 'PUT',
      body: JSON.stringify({ agent_id: agentId }),
    })
    // Refresh rooms to get updated representative_agent_id.
    // #179 — DMs (project_id=null) refresh via ``fetchAgentDMs`` only;
    // the project-scoped ``fetchRooms`` would need a non-null id.
    if (currentRoom) {
      if (currentRoom.project_id) await fetchRooms(currentRoom.project_id)
      if (currentRoom.is_dm) await fetchAgentDMs()
    }
  }, [selectedRoom, currentRoom, fetchRooms, fetchAgentDMs])

  const mentionRooms = useMemo<MentionOption[]>(
    () => Object.values(rooms).flat().map(r => ({
      id: r.id,
      display: r.name,
      kind: 'room' as const,
    })),
    [rooms],
  )

  return (
    <div className="flex h-dvh overflow-hidden">
      <SearchDialog
        open={searchOpen}
        onClose={() => setSearchOpen(false)}
        projectId={currentProjectId}
      />
      <Sidebar
        selectedRoom={selectedRoom}
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        createProjectRequest={createProjectRequest}
        createRoomRequest={createRoomRequest}
      />
      <SidebarExpandButton />

      <div className="flex min-w-0 flex-1 flex-col">
        {selectedRoom && currentRoom ? (
          <>
            <div className="relative">
              <RoomHeader
                roomName={currentRoom.name}
                connected={connected}
                // #116 — hide participant toggle, agents liveness, and
                // representative select in agent DMs (fixed 1:1, no
                // meaningful variance in any of those values).
                participantCount={
                  isDm ? undefined : Object.keys(participants).length
                }
                agentsOnline={isDm ? undefined : agentsOnline}
                agentsTotal={isDm ? undefined : agentsTotal}
                parentBreadcrumb={parentBreadcrumb}
                representativeAgentId={currentRoom.representative_agent_id}
                agentParticipants={
                  isDm
                    ? undefined
                    : user?.is_admin
                      ? agentParticipants
                      : undefined
                }
                isDm={currentRoom.is_dm}
                dmAgent={currentRoom.is_dm ? dmAgent : undefined}
                onSetRepresentative={
                  isDm
                    ? undefined
                    : user?.is_admin
                      ? handleSetRepresentative
                      : undefined
                }
                // #116 — Sub-room / Edit room / Invite links / Manage
                // agents are structurally meaningless (sub-rooms of a
                // DM, inviting a third party) or redundant (managing
                // the sole agent) in DM rooms. Drop the handlers so
                // RoomSettingsMenu hides those menu items via its
                // existing "undefined ⇒ omit" contract.
                onManageAgents={
                  isDm
                    ? undefined
                    : user?.is_admin
                      ? () => setAgentDialogOpen(true)
                      : undefined
                }
                onCreateSubRoom={
                  isDm ? undefined : () => setSubRoomDialogOpen(true)
                }
                onManageWorkspaces={
                  !isDm && (user?.is_admin || (myParticipantId && ['admin', 'owner'].includes(participants[myParticipantId]?.role ?? '')))
                    ? () => setWorkspaceRoomId(selectedRoom)
                    : undefined
                }
                onEditRoom={isDm ? undefined : () => setRoomEditOpen(true)}
                onManageInvites={
                  isDm
                    ? undefined
                    : // Match the server's auth rule in invites.py: global
                      // admin OR a room-level admin/owner Participant.
                      // The backend stays the sole authority — hiding the
                      // button is purely to avoid leading non-privileged
                      // users to a 403.
                      (() => {
                        if (user?.is_admin) return () => setRoomInvitesOpen(true)
                        const myRole = myParticipantId
                          ? participants[myParticipantId]?.role
                          : undefined
                        if (myRole === 'admin' || myRole === 'owner') {
                          return () => setRoomInvitesOpen(true)
                        }
                        return undefined
                      })()
                }
                onStopAllAgents={user?.is_admin ? async () => {
                  if (!selectedRoom) return
                  await apiFetch(`/api/v1/rooms/${selectedRoom}/stop-agents`, { method: 'POST' })
                } : undefined}
                onDeleteRoom={canRemoveParticipants ? handleDeleteRoom : undefined}
                onOpenSidebar={() => setSidebarOpen(true)}
                onToggleParticipants={
                  isDm ? undefined : () => setParticipantsOpen((v) => !v)
                }
                ephemeral={currentRoom.ephemeral ?? false}
                /* #237 — only DM owners (the user participant of the
                   DM) and admins may toggle ephemeral. Non-members
                   should not see the toggle at all. ChatPage already
                   filters DM access at the participant layer, so any
                   user who lands here on a DM is by definition a
                   member; we still gate on ``isDm`` to hide the
                   toggle on non-DM rooms (admin uses RoomEditDialog
                   for those). */
                onToggleEphemeral={
                  isDm
                    ? async (next) => {
                        try {
                          await setRoomEphemeral(currentRoom.id, next)
                        } catch (e) {
                          console.warn('setRoomEphemeral failed', e)
                        }
                      }
                    : undefined
                }
                onSearch={() => setSearchOpen(true)}
                threadDisplayMode={threadMode}
                onToggleThreadDisplayMode={() =>
                  setThreadMode(threadMode === 'panel' ? 'inline' : 'panel')
                }
                onShowArtifacts={() => setArtifactsOpen(true)}
                onShowRoomActivity={
                  user?.is_admin ? () => setRoomActivityOpen(true) : undefined
                }
                rightRailSlot={
                  <RightRailToggle
                    roomId={selectedRoom}
                    mobileOpen={rightRailOpen}
                    onMobileOpen={() => setRightRailOpen(value => !value)}
                  />
                }
              />
              {/* #116 — no participant toggle in DMs means the
                  popover has no entry point; skipping the mount
                  prevents stale ``participantsOpen=true`` from
                  silently reopening it after a room switch. */}
              {!isDm && (
                <ParticipantListPopover
                  participants={participants}
                  presence={presence}
                  open={participantsOpen}
                  onClose={() => setParticipantsOpen(false)}
                  myParticipantId={myParticipantId}
                  onRemove={canRemoveParticipants ? handleRemoveParticipant : undefined}
                />
              )}
            </div>
            <WorkspaceAttachmentBanner
              attachments={currentRoom.workspace_attachments ?? []}
            />
            <ChatArea
              messages={messages}
              participants={participants}
              myParticipantId={myParticipantId}
              typingUsers={typingUsers}
              typingStages={typingStages}
              threadIndex={threadIndex}
              activeThreadRootId={threadRootId}
              // Re-clicking the open thread's trigger is a close like any
              // other, so it routes through ``closeThread`` — clearing the
              // draft and restoring focus. Setting the id to null here
              // directly would leave a discarded draft to resurface on
              // reopen, contradicting the contract stated in the PR.
              onOpenThread={id => {
                if (threadRootId === id) closeThread()
                else setThreadRootId(id)
              }}
              renderInlineThread={
                threadMode === 'inline' && selectedRoom
                  ? root => (
                      <ThreadInline
                        root={root}
                        replies={threadIndex.repliesByRoot.get(root.id) ?? []}
                        participants={participants}
                        myParticipantId={myParticipantId}
                        roomId={selectedRoom}
                        connected={connected}
                        mentionUsers={mentionUsers}
                        mentionRooms={mentionRooms}
                        onSend={(content, metadata) =>
                          send(content, metadata, root.id)
                        }
                        onTyping={sendTyping}
                                onCollapse={closeThread}
                      />
                    )
                  : undefined
              }
            />
            <TypingIndicator
              typingUsers={typingUsers}
              typingStages={typingStages}
              participants={participants}
              myParticipantId={myParticipantId}
            />
            <MessageInput
              onSend={send}
              onTyping={sendTyping}
              disabled={!connected}
              mentionUsers={mentionUsers}
              mentionRooms={mentionRooms}
              roomId={selectedRoom}
            />
            {user?.is_admin && (
              <ManageRoomAgentsDialog
                open={agentDialogOpen}
                onOpenChange={setAgentDialogOpen}
                roomId={selectedRoom}
                participantAgentIds={participantAgentIds}
                onChange={refreshParticipants}
              />
            )}
            <RoomArtifactsDialog
              roomId={selectedRoom}
              open={artifactsOpen}
              onOpenChange={setArtifactsOpen}
            />
            {user?.is_admin && (
              <RoomActivityDialog
                roomId={selectedRoom}
                open={roomActivityOpen}
                onOpenChange={setRoomActivityOpen}
              />
            )}
            <CreateSubRoomDialog
              parentRoomId={selectedRoom}
              parentRoomName={currentRoom.name}
              myParticipantId={myParticipantId}
              open={subRoomDialogOpen}
              onOpenChange={setSubRoomDialogOpen}
              onCreated={async (newRoom) => {
                if (currentRoom) {
                  // #179 — DMs (project_id=null) refresh via fetchAgentDMs.
                  if (currentRoom.project_id) await fetchRooms(currentRoom.project_id)
                  if (currentRoom.is_dm) await fetchAgentDMs()
                }
                navigate(`/rooms/${newRoom.id}`)
              }}
            />
            <RoomEditDialog
              roomId={selectedRoom}
              open={roomEditOpen}
              onOpenChange={setRoomEditOpen}
              onSaved={() => {
                if (currentRoom) {
                  // #179 — DMs (project_id=null) refresh via fetchAgentDMs.
                  if (currentRoom.project_id) fetchRooms(currentRoom.project_id)
                  if (currentRoom.is_dm) fetchAgentDMs()
                }
              }}
            />
            <RoomInviteDialog
              roomId={selectedRoom}
              open={roomInvitesOpen}
              onOpenChange={setRoomInvitesOpen}
            />
            <RoomWorkspaceDialog
              key={selectedRoom}
              roomId={selectedRoom}
              roomName={currentRoom.name}
              open={workspaceRoomId === selectedRoom}
              onOpenChange={open => setWorkspaceRoomId(open ? selectedRoom : null)}
            />
          </>
        ) : (
          <>
            {/* Mobile-only top bar with menu button for empty state */}
            <div className="flex h-14 items-center gap-2 border-b border-[var(--color-border)] bg-[var(--color-surface)] px-2 md:hidden">
              <Button
                variant="ghost"
                size="icon"
                className="min-h-11 min-w-11"
                onClick={() => setSidebarOpen(true)}
                aria-label={t('chat.openSidebar')}
              >
                <Menu className="h-5 w-5" />
              </Button>
              <span className="text-[15px] font-bold tracking-tight">Anygarden</span>
            </div>
            <div className="flex flex-1 flex-col items-center justify-center bg-[var(--color-surface-alt)] px-6 text-center">
              <div className="flex w-full max-w-sm flex-col items-center">
                <div className="mb-5 flex h-16 w-16 items-center justify-center rounded-[var(--radius-xl)] border border-[var(--color-border)] bg-[var(--color-surface)] shadow-whisper">
                  <MessageSquare className="h-8 w-8 text-[var(--color-foreground-muted)]" />
                </div>
                <h2 className="text-lead text-[var(--color-foreground)]">{t('chat.welcome')}</h2>
                <p className="mt-2 text-sm text-[var(--color-foreground-muted)]">
                  {projects.length === 0
                    ? t('chat.emptyProjectDescription')
                    : t('chat.emptyRoomDescription')}
                </p>
                <Button
                  className="mt-6 min-h-11"
                  onClick={() => {
                    if (projects.length === 0) requestCreateProject(value => value + 1)
                    else requestCreateRoom(value => value + 1)
                  }}
                >
                  <Plus className="h-4 w-4" />
                  {projects.length === 0 ? t('chat.createProject') : t('chat.createRoom')}
                </Button>
                {projects.length > 0 && (
                  <Button
                    variant="ghost"
                    className="mt-2 min-h-11 md:hidden"
                    onClick={() => setSidebarOpen(true)}
                  >
                    {t('chat.browseRooms')}
                  </Button>
                )}
              </div>
            </div>
          </>
        )}
      </div>

      {/* #302 — right context rail. Rendered as a sibling to the main
          flex column so it can either push (desktop, expanded) or
          overlay (mobile, drawer). The rail itself early-returns null
          when ``roomId`` is null, so it's safe to mount unconditionally
          on routes that may not yet have a selected room. */}
      {/* Thread panel and context rail share the right-hand slot: at
          common laptop widths a fourth column squeezes the chat to
          ~256px and the room header overflows it. An open thread takes
          the slot; closing it restores the rail in whatever state the
          user had left it, since the rail's own collapse flag is never
          touched here. */}
      {selectedRoom && threadRoot && threadMode === 'panel' ? (
        <ThreadPanel
          root={threadRoot}
          replies={threadIndex.repliesByRoot.get(threadRoot.id) ?? []}
          participants={participants}
          myParticipantId={myParticipantId}
          roomId={selectedRoom}
          connected={connected}
          mentionUsers={mentionUsers}
          mentionRooms={mentionRooms}
          onSend={(content, metadata) => send(content, metadata, threadRoot.id)}
          onTyping={sendTyping}
          onClose={closeThread}
        />
      ) : (
        <RightContextRail
          roomId={selectedRoom}
          participants={participants}
          open={rightRailOpen}
          onClose={() => setRightRailOpen(false)}
        />
      )}
    </div>
  )
}

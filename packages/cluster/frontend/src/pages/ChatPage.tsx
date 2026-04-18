import { useState, useMemo, useEffect, useCallback } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import Sidebar from '@/components/Sidebar'
import RoomHeader from '@/components/RoomHeader'
import ChatArea from '@/components/ChatArea'
import MessageInput from '@/components/MessageInput'
import TypingIndicator from '@/components/TypingIndicator'
import ManageRoomAgentsDialog from '@/components/ManageRoomAgentsDialog'
import CreateSubRoomDialog from '@/components/CreateSubRoomDialog'
import RoomEditDialog from '@/components/RoomEditDialog'
import RoomInviteDialog from '@/components/RoomInviteDialog'
import ParticipantListPopover from '@/components/ParticipantListPopover'
import SearchDialog from '@/components/SearchDialog'
import TaskPanel from '@/components/TaskPanel'
import { Button } from '@/components/ui/button'
import { useWebSocket } from '@/hooks/useWebSocket'
import { useParticipantPresence } from '@/hooks/useParticipantPresence'
import { useRooms, type Room } from '@/hooks/useRooms'
import { useAuth } from '@/hooks/useAuth'
import { apiFetch } from '@/lib/api'
import { MessageSquare, Menu, Search, ListTodo, PanelLeftOpen } from 'lucide-react'
import type { MentionOption } from '@/components/MentionPopover'

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
}

export default function ChatPage() {
  const { roomId } = useParams<{ roomId: string }>()
  const navigate = useNavigate()
  const selectedRoom = roomId ?? null
  const { projects, rooms, agentDMs, fetchRooms, fetchAgentDMs } = useRooms()
  const { user } = useAuth()
  const { messages, connected, typingUsers, send, sendTyping } = useWebSocket(selectedRoom)
  const [participants, setParticipants] = useState<Record<string, Participant>>({})
  const [myParticipantId, setMyParticipantId] = useState<string | null>(null)
  const [agentDialogOpen, setAgentDialogOpen] = useState(false)
  const [subRoomDialogOpen, setSubRoomDialogOpen] = useState(false)
  const [roomEditOpen, setRoomEditOpen] = useState(false)
  const [roomInvitesOpen, setRoomInvitesOpen] = useState(false)
  const [participantsOpen, setParticipantsOpen] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  // Desktop-only collapsed state (#106). Persists across reloads so
  // users who prefer a wider main pane don't have to re-collapse on
  // every visit. Mobile (< md) ignores this — the off-canvas
  // ``sidebarOpen`` drawer handles visibility there; see Sidebar.tsx.
  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(() => {
    try {
      return localStorage.getItem('doorae_sidebar_collapsed') === 'true'
    } catch { return false }
  })
  const toggleSidebarCollapsed = useCallback(() => {
    setSidebarCollapsed(prev => {
      const next = !prev
      try {
        localStorage.setItem('doorae_sidebar_collapsed', String(next))
      } catch { /* ignore */ }
      return next
    })
  }, [])
  const [searchOpen, setSearchOpen] = useState(false)
  const [activeTab, setActiveTab] = useState<'chat' | 'tasks'>('chat')
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
    for (const r of (rooms[currentRoom.project_id] ?? [])) byId.set(r.id, r)
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

  // Ctrl/Cmd+B → toggle sidebar collapse (#106). Mirrors VS Code's
  // muscle memory; no text-editor bold conflict because doorae's
  // inputs are plain text.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'b') {
        e.preventDefault()
        toggleSidebarCollapsed()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [toggleSidebarCollapsed])

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

  const currentProjectId = currentRoom ? currentRoom.project_id : undefined

  const mentionUsers = useMemo<MentionOption[]>(
    () => Object.values(participants).map(p => ({
      id: p.id,
      display: p.display_name,
      kind: (p.kind === 'agent' ? 'agent' : 'user') as 'user' | 'agent',
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
        // Surface the backend's detail string via ``alert`` — other
        // chat-page flows (representative set, stop-all-agents) rely
        // on the same plain-alert escape hatch, so this stays
        // consistent. A richer toast mechanism can be retrofitted in
        // a follow-up.
        let detail = `Failed to remove participant (${resp.status})`
        try {
          const body = await resp.json()
          if (body && typeof body.detail === 'string') detail = body.detail
        } catch { /* ignore body parse */ }
        window.alert(detail)
      } catch (err) {
        window.alert(err instanceof Error ? err.message : String(err))
      }
    },
    [selectedRoom, refreshParticipants],
  )

  const handleDeleteRoom = useCallback(async () => {
    if (!selectedRoom || !currentRoom) return
    // Native confirm — same low-friction pattern the participant
    // removal flow uses. The body spells out the cascade rules so
    // the host doesn't discover them by surprise after the fact.
    const ok = window.confirm(
      `이 룸 "${currentRoom.name}"을(를) 삭제하시겠습니까?\n\n` +
        '룸의 모든 메시지가 사라지며, 하위 룸들은 최상위로 이동합니다. ' +
        '되돌릴 수 없습니다.',
    )
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
      let detail = `Failed to delete room (${resp.status})`
      try {
        const body = await resp.json()
        if (body && typeof body.detail === 'string') detail = body.detail
      } catch { /* ignore body parse */ }
      window.alert(detail)
    } catch (err) {
      window.alert(err instanceof Error ? err.message : String(err))
    }
  }, [selectedRoom, currentRoom, navigate])

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
    window.addEventListener('doorae:room:deleted', onDeleted)
    return () => window.removeEventListener('doorae:room:deleted', onDeleted)
  }, [selectedRoom, navigate])

  const handleSetRepresentative = useCallback(async (agentId: string | null) => {
    if (!selectedRoom) return
    await apiFetch(`/api/v1/rooms/${selectedRoom}/representative`, {
      method: 'PUT',
      body: JSON.stringify({ agent_id: agentId }),
    })
    // Refresh rooms to get updated representative_agent_id
    if (currentRoom) {
      await fetchRooms(currentRoom.project_id)
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
        collapsed={sidebarCollapsed}
        onToggleCollapsed={toggleSidebarCollapsed}
      />

      {/* Floating expand button (#106). Appears only when the
          desktop sidebar is collapsed. Hidden on mobile — the
          off-canvas flow uses the RoomHeader hamburger / empty-
          state menu instead. z-30 keeps it below the sidebar
          (z-40) so an animating sidebar overlays it naturally. */}
      {sidebarCollapsed && (
        <button
          type="button"
          onClick={toggleSidebarCollapsed}
          aria-label="Expand sidebar"
          data-testid="sidebar-expand"
          title="Expand sidebar (⌘B)"
          className="hidden md:inline-flex fixed left-2 top-2 z-30 items-center justify-center rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-white p-1.5 text-[var(--color-foreground-muted)] shadow-whisper hover:bg-black/5 hover:text-[var(--color-foreground)] transition-colors"
        >
          <PanelLeftOpen className="h-4 w-4" />
        </button>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        {selectedRoom && currentRoom ? (
          <>
            <div className="relative">
              <RoomHeader
                roomName={currentRoom.name}
                connected={connected}
                participantCount={Object.keys(participants).length}
                agentsOnline={agentsOnline}
                agentsTotal={agentsTotal}
                parentBreadcrumb={parentBreadcrumb}
                representativeAgentId={currentRoom.representative_agent_id}
                agentParticipants={user?.is_admin ? agentParticipants : undefined}
                isDm={currentRoom.is_dm}
                dmAgent={currentRoom.is_dm ? dmAgent : undefined}
                onSetRepresentative={user?.is_admin ? handleSetRepresentative : undefined}
                onManageAgents={user?.is_admin ? () => setAgentDialogOpen(true) : undefined}
                onCreateSubRoom={() => setSubRoomDialogOpen(true)}
                onEditRoom={() => setRoomEditOpen(true)}
                onManageInvites={
                  // Match the server's auth rule in invites.py: global
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
                onToggleParticipants={() => setParticipantsOpen((v) => !v)}
              />
              <ParticipantListPopover
                participants={participants}
                presence={presence}
                open={participantsOpen}
                onClose={() => setParticipantsOpen(false)}
                myParticipantId={myParticipantId}
                onRemove={canRemoveParticipants ? handleRemoveParticipant : undefined}
              />
            </div>
            {/* Chat / Tasks tab bar + Search */}
            <div className="flex items-center justify-between border-b border-[var(--color-border)] px-4">
              <div className="flex items-center gap-1">
                <button
                  onClick={() => setActiveTab('chat')}
                  className={`flex items-center gap-1.5 px-3 py-2 text-xs font-medium border-b-2 transition-colors ${
                    activeTab === 'chat'
                      ? 'border-[var(--color-brand)] text-[var(--color-brand)]'
                      : 'border-transparent text-[var(--color-foreground-muted)] hover:text-[var(--color-foreground)]'
                  }`}
                >
                  <MessageSquare className="h-3.5 w-3.5" /> Chat
                </button>
                <button
                  onClick={() => setActiveTab('tasks')}
                  className={`flex items-center gap-1.5 px-3 py-2 text-xs font-medium border-b-2 transition-colors ${
                    activeTab === 'tasks'
                      ? 'border-[var(--color-brand)] text-[var(--color-brand)]'
                      : 'border-transparent text-[var(--color-foreground-muted)] hover:text-[var(--color-foreground)]'
                  }`}
                >
                  <ListTodo className="h-3.5 w-3.5" /> Tasks
                </button>
              </div>
              <button
                onClick={() => setSearchOpen(true)}
                className="flex items-center gap-1.5 rounded-[var(--radius-sm)] px-2 py-1 text-xs text-[var(--color-foreground-muted)] hover:bg-black/5"
              >
                <Search className="h-3.5 w-3.5" />
                <span className="hidden sm:inline">Search</span>
                <kbd className="hidden rounded border border-[var(--color-border)] px-1 py-0.5 text-[10px] sm:inline">⌘K</kbd>
              </button>
            </div>
            {activeTab === 'chat' ? (
              <>
                <ChatArea
                  messages={messages}
                  participants={participants}
                  myParticipantId={myParticipantId}
                  typingUsers={typingUsers}
                />
                <TypingIndicator
                  typingUsers={typingUsers}
                  participants={participants}
                  myParticipantId={myParticipantId}
                />
                <MessageInput
                  onSend={send}
                  onTyping={sendTyping}
                  disabled={!connected}
                  mentionUsers={mentionUsers}
                  mentionRooms={mentionRooms}
                />
              </>
            ) : (
              <TaskPanel roomId={selectedRoom} />
            )}
            {user?.is_admin && (
              <ManageRoomAgentsDialog
                open={agentDialogOpen}
                onOpenChange={setAgentDialogOpen}
                roomId={selectedRoom}
                participantAgentIds={participantAgentIds}
                onChange={refreshParticipants}
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
                  await fetchRooms(currentRoom.project_id)
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
                  fetchRooms(currentRoom.project_id)
                  if (currentRoom.is_dm) fetchAgentDMs()
                }
              }}
            />
            <RoomInviteDialog
              roomId={selectedRoom}
              open={roomInvitesOpen}
              onOpenChange={setRoomInvitesOpen}
            />
          </>
        ) : (
          <>
            {/* Mobile-only top bar with menu button for empty state */}
            <div className="flex h-14 items-center gap-2 border-b border-[var(--color-border)] bg-white px-4 md:hidden">
              <Button
                variant="ghost"
                size="icon"
                onClick={() => setSidebarOpen(true)}
                aria-label="Open sidebar"
              >
                <Menu className="h-5 w-5" />
              </Button>
              <span className="text-[15px] font-bold tracking-tight">Doorae</span>
            </div>
            <div className="flex flex-1 flex-col items-center justify-center bg-[var(--color-surface-alt)] px-6 text-center">
              <MessageSquare className="mb-4 h-16 w-16 text-[var(--color-foreground-subtle)] opacity-70" />
              <h2 className="text-body-lg text-[var(--color-foreground)]">Welcome to Doorae</h2>
              <p className="text-caption mt-2">Select a room from the sidebar to start chatting.</p>
              {projects.length === 0 && (
                <p className="text-caption mt-1">Create a project first to get started.</p>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}

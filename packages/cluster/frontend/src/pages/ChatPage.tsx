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
import SearchDialog from '@/components/SearchDialog'
import TaskPanel from '@/components/TaskPanel'
import { Button } from '@/components/ui/button'
import { useWebSocket } from '@/hooks/useWebSocket'
import { useRooms, type Room } from '@/hooks/useRooms'
import { useAuth } from '@/hooks/useAuth'
import { apiFetch } from '@/lib/api'
import { MessageSquare, Menu, Search, ListTodo } from 'lucide-react'
import type { MentionOption } from '@/components/MentionPopover'

export interface Participant {
  id: string
  display_name: string
  kind: string
  user_id?: string
  agent_id?: string
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
  const [sidebarOpen, setSidebarOpen] = useState(false)
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

  const agentParticipants = useMemo(
    () => Object.values(participants)
      .filter(p => p.kind === 'agent' && p.agent_id)
      .map(p => ({ id: p.id, agent_id: p.agent_id!, display_name: p.display_name })),
    [participants],
  )

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
      />

      <div className="flex min-w-0 flex-1 flex-col">
        {selectedRoom && currentRoom ? (
          <>
            <RoomHeader
              roomName={currentRoom.name}
              connected={connected}
              participantCount={Object.keys(participants).length}
              parentBreadcrumb={parentBreadcrumb}
              representativeAgentId={currentRoom.representative_agent_id}
              agentParticipants={user?.is_admin ? agentParticipants : undefined}
              onSetRepresentative={user?.is_admin ? handleSetRepresentative : undefined}
              onManageAgents={user?.is_admin ? () => setAgentDialogOpen(true) : undefined}
              onCreateSubRoom={() => setSubRoomDialogOpen(true)}
              onEditRoom={() => setRoomEditOpen(true)}
              onStopAllAgents={user?.is_admin ? async () => {
                if (!selectedRoom) return
                await apiFetch(`/api/v1/rooms/${selectedRoom}/stop-agents`, { method: 'POST' })
              } : undefined}
              onOpenSidebar={() => setSidebarOpen(true)}
            />
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

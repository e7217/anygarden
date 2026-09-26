import { useState, useEffect, useMemo, useCallback, useRef } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { useAuth } from '@/hooks/useAuth'
import { useSystemVersion, useUpdateStatus } from '@/hooks/useSystemVersion'
import { useAgents, type Agent } from '@/hooks/useAgents'
import { useRooms, type Room } from '@/hooks/useRooms'
import { useSidebarLayout } from '@/hooks/useSidebarLayout'
import { apiFetch } from '@/lib/api'
import { agentStatusLabel, deriveAgentOnline } from '@/lib/agent-liveness'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Input } from '@/components/ui/input'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogTrigger,
} from '@/components/ui/dialog'
import PresenceDot from '@/components/PresenceDot'
import UpdateDot from '@/components/UpdateDot'
import { EntityAvatar, type AvatarKind } from '@/components/EntityAvatar'
import RoomEditDialog from '@/components/RoomEditDialog'
import SidebarProjectMenu from '@/components/SidebarProjectMenu'
import SidebarRoomMenu from '@/components/SidebarRoomMenu'
import SidebarAdminMenu from '@/components/SidebarAdminMenu'
import AgentSettingsMenu from '@/components/AgentSettingsMenu'
import AgentSettingsDialog from '@/components/AgentSettingsDialog'
import { useLocale } from '@/i18n/LocaleProvider'
import { LocaleToggle } from '@/i18n/LocaleToggle'
import { ThemeToggle } from '@/theme/ThemeToggle'
import { useFeedback } from '@/components/feedback/FeedbackProvider'
import {
  Hash, Plus, ChevronDown, ChevronRight, LogOut, MessageSquare, X,
  Pin, PinOff, GripVertical, PanelLeftClose,
} from 'lucide-react'
import {
  DndContext, closestCenter, KeyboardSensor, PointerSensor,
  useSensor, useSensors,
  type DragEndEvent,
} from '@dnd-kit/core'
import {
  arrayMove, SortableContext, sortableKeyboardCoordinates,
  verticalListSortingStrategy, useSortable,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'

// Tree node for rendering the sidebar room list. The server
// returns a flat list per project; we reshape it here so each
// node carries its children. Depth starts at 0 for rooms that
// sit directly under a project (``parent_room_id === null``)
// and increments with each sub-room level.
interface RoomTreeNode {
  room: Room
  depth: number
  children: RoomTreeNode[]
}

/**
 * Build a parent → children tree from the flat room list.
 *
 * Orphans (a room whose ``parent_room_id`` points at a room
 * that isn't in the list — possible if the admin deleted the
 * parent and the cascade-detach turned ``parent_room_id`` to
 * NULL, or if the user can see the child but not the parent
 * because of permissions) are promoted to depth 0 so they
 * still show up in the sidebar instead of being silently
 * hidden.
 */
function buildRoomTree(rooms: Room[]): RoomTreeNode[] {
  const byId = new Map<string, RoomTreeNode>()
  for (const r of rooms) {
    byId.set(r.id, { room: r, depth: 0, children: [] })
  }

  const roots: RoomTreeNode[] = []
  for (const node of byId.values()) {
    const parentId = node.room.parent_room_id ?? null
    if (parentId && byId.has(parentId)) {
      const parent = byId.get(parentId)!
      parent.children.push(node)
    } else {
      // Either genuinely top-level or a visible orphan whose
      // parent is unreachable. Either way: render at the root.
      roots.push(node)
    }
  }

  // Assign depth with a BFS walk from the roots so every node
  // has an absolute depth regardless of insertion order.
  const assignDepth = (node: RoomTreeNode, depth: number): void => {
    node.depth = depth
    // Sort children by name for a deterministic sidebar order.
    node.children.sort((a, b) => a.room.name.localeCompare(b.room.name))
    for (const child of node.children) {
      assignDepth(child, depth + 1)
    }
  }
  roots.sort((a, b) => a.room.name.localeCompare(b.room.name))
  for (const root of roots) assignDepth(root, 0)

  return roots
}

/**
 * Resolve the Agent backing a given DM room (#71).
 *
 * The DM-room tuple from ``useRooms().agentDMs`` doesn't include
 * the agent's lifecycle state — it comes from
 * ``useAgents()`` (admin-gated). We bridge the two by:
 *   1. ``representative_agent_id`` (authoritative; server sets
 *      this on DM creation).
 *   2. Fallback to name match stripping the ``"DM: "`` prefix —
 *      covers older DMs where the representative agent wasn't
 *      persisted. Brittle if the admin renames an agent, but
 *      falls through to "offline" which is the safe default.
 */
function findAgentForDM(dm: Room, agents: Agent[]): Agent | undefined {
  if (dm.representative_agent_id) {
    const byId = agents.find(a => a.id === dm.representative_agent_id)
    if (byId) return byId
  }
  const target = dm.name.replace(/^DM:\s*/, '').trim()
  if (!target) return undefined
  return agents.find(a => a.name === target)
}

// Split the flat room list into the sidebar's top "Pinned" section
// and the default section (#47). Pinned covers top-level rooms only
// — sub-rooms always render under their parent, preserving the
// tree, even if the parent is pinned. Ordering within the pinned
// section follows ``sort_order`` (sparse integer) ascending.
function splitPinned(rooms: Room[]): { pinned: Room[]; rest: Room[] } {
  const pinned: Room[] = []
  const rest: Room[] = []
  for (const r of rooms) {
    if (r.pinned && !r.parent_room_id) pinned.push(r)
    else rest.push(r)
  }
  pinned.sort(
    (a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0),
  )
  return { pinned, rest }
}

interface SidebarProps {
  selectedRoom: string | null
  /** Mobile off-canvas open state. Desktop (md+) is always visible. */
  open?: boolean
  onClose?: () => void
  /** Requests from the empty chat state reuse the sidebar's dialogs. */
  createProjectRequest?: number
  createRoomRequest?: number
}

export default function Sidebar({
  selectedRoom,
  open = false,
  onClose,
  createProjectRequest = 0,
  createRoomRequest = 0,
}: SidebarProps) {
  const { t } = useLocale()
  const { confirm, notify } = useFeedback()
  const { user, logout } = useAuth()
  // #115 — desktop collapse state/toggle/Ctrl+B now live in a shared
  // provider so every page hosting <Sidebar> gets identical behaviour
  // without prop drilling. The hook throws when used outside the
  // provider, matching useRooms's discipline.
  const { collapsed, toggleCollapsed } = useSidebarLayout()

  // Ctrl/Cmd+B → toggle collapse (#106). Registered here so the
  // shortcut only binds on routes that actually render <Sidebar>
  // (Login/Guest pages share the provider but don't mount Sidebar,
  // so Cmd+B stays a no-op there instead of eating the keystroke).
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'b') {
        e.preventDefault()
        toggleCollapsed()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [toggleCollapsed])
  const {
    projects, rooms, agentDMs, createProject, deleteProject, createRoom, fetchRooms,
    pinRoom, reorderPinnedRooms,
  } = useRooms()
  const navigate = useNavigate()
  const location = useLocation()
  const isAdmin = !!user?.is_admin

  // #546 — server version (all users) + admin-only cached update status.
  const serverVersion = useSystemVersion()
  const { updates } = useUpdateStatus(isAdmin)
  const updateAvailable = updates.some((u) => u.update_available)

  // Edit dialog state lives at the sidebar root (not inside
  // ``SidebarRoomMenu``) so closing the menu doesn't unmount the
  // dialog while the user is typing. ``editRoomId === null`` means
  // the dialog is closed.
  const [editRoomId, setEditRoomId] = useState<string | null>(null)

  // Pointer needs a small drag-distance activation so a simple
  // click on the room label still navigates — only a genuine drag
  // (>= 6px) kicks the DnD session off. Keyboard sensor gives
  // Space/Enter pick-up and arrow-key movement out of the box.
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )

  const [expandedProjects, setExpandedProjects] = useState<Set<string>>(() => {
    try {
      const saved = localStorage.getItem('anygarden_expanded_projects')
      return saved ? new Set(JSON.parse(saved)) : new Set()
    } catch { return new Set() }
  })

  useEffect(() => {
    try {
      localStorage.setItem(
        'anygarden_expanded_projects',
        JSON.stringify(Array.from(expandedProjects)),
      )
    } catch { /* ignore */ }
  }, [expandedProjects])
  const [agentsExpanded, setAgentsExpanded] = useState(true)
  const [newProjectName, setNewProjectName] = useState('')
  const [newRoomName, setNewRoomName] = useState('')
  const [roomProjectId, setRoomProjectId] = useState('')
  const [projectDialogOpen, setProjectDialogOpen] = useState(false)
  const [roomDialogOpen, setRoomDialogOpen] = useState(false)
  const [roomNeedsProjectChoice, setRoomNeedsProjectChoice] = useState(false)
  const [roomFromEmptyState, setRoomFromEmptyState] = useState(false)
  const lastProjectRequest = useRef(0)
  const lastRoomRequest = useRef(0)

  useEffect(() => {
    if (createProjectRequest === 0 || createProjectRequest === lastProjectRequest.current) return
    lastProjectRequest.current = createProjectRequest
    setNewProjectName('')
    setProjectDialogOpen(true)
  }, [createProjectRequest])

  useEffect(() => {
    if (createRoomRequest === 0 || createRoomRequest === lastRoomRequest.current) return
    lastRoomRequest.current = createRoomRequest
    setRoomProjectId(projects.length === 1 ? projects[0].id : '')
    setRoomNeedsProjectChoice(projects.length > 1)
    setRoomFromEmptyState(true)
    setNewRoomName('')
    setRoomDialogOpen(true)
  }, [createRoomRequest, projects])
  // Delete-project confirmation target. ``null`` means the dialog
  // is closed. When set, the dialog shows the cascade warning if
  // the project has any rooms (``rooms[id].length > 0``) and a
  // plain confirmation otherwise.
  const [deleteProjectTarget, setDeleteProjectTarget] = useState<
    { id: string; name: string } | null
  >(null)

  const toggleProject = (id: string) => {
    setExpandedProjects(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  // Pre-compute the tree for each project so the render loop
  // does not recompute on every draw. Keyed by project_id so
  // we get a stable reference per project for
  // ``RoomTreeBranch``. Pinned top-level rooms are lifted into a
  // separate section and skipped here so they don't double up.
  const { pinnedRooms, projectTrees } = useMemo(() => {
    const allPinned: Room[] = []
    const trees: Record<string, RoomTreeNode[]> = {}
    for (const projectId of Object.keys(rooms)) {
      const list = rooms[projectId] ?? []
      const { pinned, rest } = splitPinned(list)
      allPinned.push(...pinned)
      trees[projectId] = buildRoomTree(rest)
    }
    // Global pin order: preserve per-user sort_order across
    // projects. Same sparse integer spacing as the server.
    allPinned.sort(
      (a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0),
    )
    return { pinnedRooms: allPinned, projectTrees: trees }
  }, [rooms])

  // Mirror of ChatPage.handleDeleteRoom (see ChatPage.tsx:217-250).
  // Copied rather than extracted to a shared hook because the
  // sidebar's control flow (navigate only when the deleted room is
  // the selected one; refetch the project's rooms regardless) does
  // not line up with ChatPage's single-room context. Keeping it
  // inlined keeps the two call sites independently evolvable.
  const handleDeleteRoom = useCallback(async (roomId: string, projectId: string, roomName: string) => {
    const ok = await confirm({
      title: t('chat.deleteRoomTitle', { name: roomName }),
      description: t('chat.deleteRoomDescription'),
      confirmLabel: t('chat.delete'),
      destructive: true,
    })
    if (!ok) return
    try {
      const resp = await apiFetch(`/api/v1/rooms/${roomId}`, { method: 'DELETE' })
      if (resp.status === 204) {
        // The WS ``room_deleted`` broadcast will also trigger a
        // full ``refetch`` via RoomsProvider's invalidate listener.
        // The explicit fetchRooms here keeps the UI snappy for the
        // acting user without waiting on the round-trip.
        await fetchRooms(projectId)
        if (selectedRoom === roomId) navigate('/')
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
  }, [fetchRooms, navigate, selectedRoom, t, confirm, notify])

  // ``editRoomId`` alone isn't enough for the refetch-after-save
  // callback — we need the project id too. Build a room→project
  // map from the same ``rooms`` store the tree renders from.
  const roomProjectLookup = useMemo(() => {
    const m = new Map<string, string>()
    for (const [pid, list] of Object.entries(rooms)) {
      for (const r of list ?? []) m.set(r.id, pid)
    }
    return m
  }, [rooms])

  const handleCreateProject = async () => {
    if (!newProjectName.trim()) return
    try {
      await createProject(newProjectName.trim())
      setNewProjectName('')
      setProjectDialogOpen(false)
    } catch { /* ignore */ }
  }

  // Called from the confirm dialog once the user accepts the
  // cascade warning. Snapshot the affected room ids BEFORE the
  // delete so the post-delete navigate check still works — the
  // optimistic local-state drop in ``deleteProject`` clears
  // ``rooms[projectId]`` immediately.
  const handleDeleteProject = useCallback(async (projectId: string) => {
    const affected = new Set((rooms[projectId] ?? []).map(r => r.id))
    try {
      await deleteProject(projectId)
      setDeleteProjectTarget(null)
      // If the user was viewing a room inside the deleted project,
      // route them to the home screen — staying on a now-gone room
      // would render a permanent 404 shell.
      if (selectedRoom && affected.has(selectedRoom)) navigate('/')
      // Drop the project from the expanded set so re-creating a
      // project with the same id later doesn't inherit a stale flag.
      setExpandedProjects(prev => {
        if (!prev.has(projectId)) return prev
        const next = new Set(prev)
        next.delete(projectId)
        return next
      })
    } catch (err) {
      notify({ message: err instanceof Error ? err.message : String(err), tone: 'error' })
    }
  }, [deleteProject, navigate, rooms, selectedRoom, notify])

  const handleCreateRoom = async () => {
    if (!newRoomName.trim() || !roomProjectId) return
    try {
      const room = await createRoom(roomProjectId, newRoomName.trim())
      setNewRoomName('')
      setRoomDialogOpen(false)
      if (roomFromEmptyState && room?.id) {
        navigate(`/rooms/${room.id}`)
        onClose?.()
      }
      setRoomFromEmptyState(false)
    } catch { /* ignore */ }
  }

  const openNewRoomDialog = (projectId: string) => {
    setRoomProjectId(projectId)
    setRoomNeedsProjectChoice(false)
    setRoomFromEmptyState(false)
    setNewRoomName('')
    setRoomDialogOpen(true)
  }

  // Close the drawer when navigating on mobile.
  const go = (path: string) => {
    navigate(path)
    onClose?.()
  }

  return (
    <>
      {/* Mobile backdrop */}
      {open && (
        <button
          type="button"
          aria-label={t('chat.closeSidebar')}
          className="fixed inset-0 z-30 bg-black/25 backdrop-blur-[1px] md:hidden"
          onClick={onClose}
        />
      )}

      <aside
        data-testid="sidebar-root"
        aria-hidden={collapsed || undefined}
        className={`
          fixed inset-y-0 left-0 z-40 flex h-full w-64 flex-col border-r border-[var(--color-border)] bg-[var(--color-surface-alt)]
          transform ${open ? 'transition-transform duration-200 ease-out' : 'transition-none'}
          ${open ? 'translate-x-0 shadow-deep' : '-translate-x-full'}
          ${collapsed
            ? 'md:-translate-x-full md:w-0 md:overflow-hidden md:border-r-0'
            : 'md:static md:z-auto md:translate-x-0 md:w-64'}
        `}
      >
      {/* A consistent home target anchors navigation even in an empty workspace. */}
      <div className="flex h-14 items-center justify-between gap-2 px-3">
        <button
          type="button"
          onClick={() => go('/')}
          aria-label={t('chat.goHome')}
          className="flex min-h-11 min-w-0 items-center rounded-[var(--radius-sm)] px-2 text-left text-[15px] font-bold tracking-tight text-[var(--color-foreground)] hover:bg-[var(--color-surface-hover)]"
        >
          <span className="truncate">Anygarden</span>
        </button>
        <div className="flex items-center gap-1">
          {/* Desktop collapse trigger (#106). Paired with the
              main-area floating expand button so users can toggle
              from either side. Hidden below ``md:`` — mobile uses
              the X close button to the right. */}
          <button
            type="button"
            className="hidden h-11 w-11 items-center justify-center rounded-[var(--radius-sm)] text-[var(--color-foreground-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-foreground)] transition-colors md:inline-flex"
            onClick={toggleCollapsed}
            aria-label={t('chat.collapseSidebar')}
            data-testid="sidebar-collapse"
            title={t('chat.collapseSidebarShortcut')}
          >
            <PanelLeftClose className="h-4 w-4" />
          </button>
          <button
            type="button"
            className="flex h-11 w-11 items-center justify-center rounded-[var(--radius-sm)] text-[var(--color-foreground-muted)] hover:bg-[var(--color-surface-hover)] md:hidden"
            onClick={onClose}
            aria-label={t('chat.closeSidebar')}
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>

      {/* Projects & Rooms */}
      <ScrollArea className="min-h-0 flex-1">
        <div className="px-2 py-2">
          {/* Pinned section — top-level pinned rooms across all
              projects, ordered by ``sort_order`` (#47). The
              ``DndContext`` scope is intentionally local to the
              pinned section so drags outside the section never
              trigger reorder logic. */}
          {pinnedRooms.length > 0 && (
            <div className="mb-2">
              <div className="flex items-center gap-1 px-2 py-1 text-badge uppercase text-[var(--color-foreground-muted)]">
                <Pin className="h-3 w-3" />
                {t('chat.pinned')}
              </div>
              <DndContext
                sensors={sensors}
                collisionDetection={closestCenter}
                onDragEnd={(event: DragEndEvent) => {
                  const { active, over } = event
                  if (!over || active.id === over.id) return
                  const ids = pinnedRooms.map(r => r.id)
                  const from = ids.indexOf(String(active.id))
                  const to = ids.indexOf(String(over.id))
                  if (from === -1 || to === -1) return
                  const nextOrder = arrayMove(ids, from, to)
                  void reorderPinnedRooms(nextOrder)
                }}
              >
                <SortableContext
                  items={pinnedRooms.map(r => r.id)}
                  strategy={verticalListSortingStrategy}
                >
                  <div className="flex flex-col gap-0.5">
                    {pinnedRooms.map(room => (
                      <PinnedRoomItem
                        key={room.id}
                        room={room}
                        selectedRoom={selectedRoom}
                        onGo={go}
                        onUnpin={() => { void pinRoom(room.id, false) }}
                      />
                    ))}
                  </div>
                </SortableContext>
              </DndContext>
            </div>
          )}

          {projects.map(project => (
            <div key={project.id} className="mb-1">
              {/* Project header row: the expand/collapse <button>
                  and the overflow-menu trigger live side-by-side
                  inside a ``group`` container so the menu fades
                  in on hover without nesting interactive elements
                  (which would be invalid HTML). ``relative`` anchors
                  the menu's absolute-positioned popover. */}
              <div className="group relative flex items-center rounded-[var(--radius-sm)] hover:bg-[var(--color-surface-hover)] transition-colors">
                <button
                  onClick={() => toggleProject(project.id)}
                  className="text-sm font-medium flex min-h-11 flex-1 min-w-0 items-center px-2 text-[var(--color-foreground)]"
                >
                  {expandedProjects.has(project.id)
                    ? <ChevronDown className="mr-1 h-4 w-4 shrink-0 text-[var(--color-foreground-subtle)]" />
                    : <ChevronRight className="mr-1 h-4 w-4 shrink-0 text-[var(--color-foreground-subtle)]" />
                  }
                  <span className="truncate">{project.name}</span>
                </button>
                <SidebarProjectMenu
                  projectId={project.id}
                  onDelete={() => setDeleteProjectTarget({ id: project.id, name: project.name })}
                />
              </div>

              {expandedProjects.has(project.id) && (
                <div className="ml-3 mt-0.5 flex flex-col gap-0.5">
                  <RoomTreeBranch
                    nodes={projectTrees[project.id] ?? []}
                    selectedRoom={selectedRoom}
                    onGo={go}
                    onPin={(roomId) => { void pinRoom(roomId, true) }}
                    isAdmin={isAdmin}
                    projectId={project.id}
                    onRename={(roomId) => setEditRoomId(roomId)}
                    onDelete={(roomId, name) => {
                      void handleDeleteRoom(roomId, project.id, name)
                    }}
                  />

                  <button
                    onClick={() => openNewRoomDialog(project.id)}
                    className="flex min-h-11 w-full items-center rounded-[var(--radius-sm)] px-2 text-[14px] font-medium text-[var(--color-foreground-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-foreground)] transition-colors"
                  >
                    <Plus className="mr-1.5 h-3.5 w-3.5 shrink-0 text-[var(--color-foreground-subtle)]" />
                    <span>{t('chat.newRoom')}</span>
                  </button>
                </div>
              )}
            </div>
          ))}

          {projects.length === 0 && (
            <p className="text-caption text-[var(--color-foreground-muted)] px-2 py-4 text-center">
              {t('chat.noProjects')}
            </p>
          )}
        </div>
      </ScrollArea>

      {/* New Project button */}
      <div className="p-2">
        <Dialog open={projectDialogOpen} onOpenChange={setProjectDialogOpen}>
          <DialogTrigger asChild>
            <Button variant="ghost" size="sm" className="min-h-11 w-full justify-start text-[var(--color-foreground-muted)]">
              <Plus className="mr-2 h-4 w-4" />
              {t('chat.newProject')}
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{t('chat.createProject')}</DialogTitle>
            </DialogHeader>
            <div className="space-y-4 py-2">
              <Input
                placeholder={t('chat.projectName')}
                value={newProjectName}
                onChange={e => setNewProjectName(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && handleCreateProject()}
              />
            </div>
            <DialogFooter>
              <Button onClick={handleCreateProject} disabled={!newProjectName.trim()}>
                {t('chat.create')}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>

      {/* Rename room dialog — reused from ChatPage's room settings
          menu. Rendered only while ``editRoomId`` is set so the
          load-on-mount effect inside the dialog fires once per
          edit session, matching ChatPage's usage pattern. */}
      {editRoomId && (
        <RoomEditDialog
          roomId={editRoomId}
          open={editRoomId !== null}
          onOpenChange={(o) => { if (!o) setEditRoomId(null) }}
          onSaved={() => {
            const pid = roomProjectLookup.get(editRoomId)
            if (pid) void fetchRooms(pid)
          }}
        />
      )}

      {/* New Room dialog */}
      <Dialog open={roomDialogOpen} onOpenChange={(next) => {
        setRoomDialogOpen(next)
        if (!next) setRoomFromEmptyState(false)
      }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('chat.createRoom')}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            {roomNeedsProjectChoice && (
              <div className="space-y-2">
                <label htmlFor="sidebar-new-room-project" className="block text-sm font-medium">{t('chat.project')}</label>
                <select
                  id="sidebar-new-room-project"
                  value={roomProjectId}
                  onChange={(event) => setRoomProjectId(event.target.value)}
                  className="h-11 w-full rounded-[var(--radius-xs)] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-3 text-sm"
                >
                  <option value="">{t('chat.selectProject')}</option>
                  {projects.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}
                </select>
              </div>
            )}
            <Input
              placeholder={t('chat.roomName')}
              value={newRoomName}
              onChange={e => setNewRoomName(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleCreateRoom()}
            />
          </div>
          <DialogFooter>
            <Button onClick={handleCreateRoom} disabled={!newRoomName.trim() || !roomProjectId}>
              {t('chat.create')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete project confirmation dialog. Message branches on
          whether the project has any rooms — a plain confirmation
          for empty projects, a cascade warning (with the exact
          room count) when rooms would be removed alongside it. */}
      <Dialog
        open={deleteProjectTarget !== null}
        onOpenChange={(o) => { if (!o) setDeleteProjectTarget(null) }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('chat.deleteProject')}</DialogTitle>
          </DialogHeader>
          {deleteProjectTarget && (() => {
            const roomCount = (rooms[deleteProjectTarget.id] ?? []).length
            return (
              <div className="space-y-2 py-2 text-sm text-[var(--color-foreground)]">
                {roomCount === 0 ? (
                  <p>{t('chat.deleteProjectEmptyPrompt', { name: deleteProjectTarget.name })}</p>
                ) : (
                  <p>{t('chat.deleteProjectRoomsPrompt', { name: deleteProjectTarget.name, count: roomCount })}</p>
                )}
                <p className="text-[var(--color-foreground-muted)]">{t('chat.cannotUndo')}</p>
              </div>
            )
          })()}
          <DialogFooter>
            <Button variant="ghost" onClick={() => setDeleteProjectTarget(null)}>
              {t('chat.cancel')}
            </Button>
            <Button
              variant="destructive"
              data-testid="sidebar-project-delete-confirm"
              onClick={() => {
                if (deleteProjectTarget) void handleDeleteProject(deleteProjectTarget.id)
              }}
            >
              {t('chat.delete')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Agents DM section.
          Admin users see presence dots next to each DM (driven by
          ``useAgents()`` — an admin-gated endpoint). Non-admins
          get the same DM list without dots, to avoid a 403 on the
          agents fetch and to avoid a misleading "offline
          everywhere" dot for guest sessions. See #71. */}
      {agentDMs.length > 0 && (
        <div className="border-t border-[var(--color-border)] px-2 py-2">
          <button
            onClick={() => setAgentsExpanded(prev => !prev)}
            className="flex min-h-11 w-full items-center gap-1 px-2 text-badge uppercase text-[var(--color-foreground-muted)] hover:text-[var(--color-foreground)] transition-colors"
          >
            {agentsExpanded
              ? <ChevronDown className="h-3 w-3" />
              : <ChevronRight className="h-3 w-3" />}
            {t('chat.agents')}
          </button>
          {agentsExpanded && (
            isAdmin ? (
              <AgentDMListAdmin
                dms={agentDMs}
                selectedRoom={selectedRoom}
                onGo={go}
              />
            ) : (
              <div className="flex flex-col gap-0.5">
                {agentDMs.map(dm => {
                  const label = dm.name.replace(/^DM:\s*/, '')
                  return (
                    <button
                      key={dm.id}
                      onClick={() => go(`/rooms/${dm.id}`)}
                      data-testid={`sidebar-dm-${dm.id}`}
                      className={`flex min-h-11 w-full items-center gap-2 rounded-[var(--radius-sm)] px-2 text-[14px] font-medium transition-colors ${
                        selectedRoom === dm.id
                          ? 'bg-[var(--color-surface)] shadow-whisper text-[var(--color-foreground)]'
                          : 'text-[var(--color-foreground-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-foreground)]'
                      }`}
                    >
                      <EntityAvatar
                        id={dm.representative_agent_id ?? dm.id}
                        name={label}
                        kind="agent"
                        size="xs"
                      />
                      <span className="min-w-0 truncate">{label}</span>
                      {dm.has_updates && <UpdateDot className="ml-auto" />}
                    </button>
                  )
                })}
              </div>
            )
          )}
        </div>
      )}

      <div className="flex items-center justify-between gap-2 border-t border-[var(--color-border)] px-3 py-2">
        <LocaleToggle compact />
        <ThemeToggle className="min-h-11 min-w-11" />
      </div>

      {/* User info */}
      <div className="relative flex items-center justify-between gap-2 border-t border-[var(--color-border)] px-3 py-2">
        <div className="flex min-w-0 flex-col">
          <span className="truncate text-xs text-[var(--color-foreground-muted)]">{user?.email}</span>
          {serverVersion && (
            <span className="truncate text-[11px] text-[var(--color-foreground-subtle)]" title={t('chat.serverVersion')}>
              anygarden v{serverVersion}
            </span>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {isAdmin && (
            <SidebarAdminMenu
              pathname={location.pathname}
              updateAvailable={updateAvailable}
              onGo={go}
            />
          )}
          <Button variant="ghost" size="icon" onClick={logout} title={t('chat.logout')} aria-label={t('chat.logout')} className="min-h-11 min-w-11">
            <LogOut className="h-4 w-4" />
          </Button>
        </div>
      </div>
      </aside>
    </>
  )
}

/**
 * Recursive renderer for the sidebar room tree. Expects the
 * pre-built list from ``buildRoomTree`` (roots only — children
 * are rendered by this component's own recursion).
 *
 * Indentation: 12px per depth level, capped so deeply-nested
 * threads don't push the room label off the visible area in
 * the 256px sidebar.
 */
interface RoomTreeBranchProps {
  nodes: RoomTreeNode[]
  selectedRoom: string | null
  onGo: (path: string) => void
  onPin: (roomId: string) => void
  isAdmin: boolean
  projectId: string
  onRename: (roomId: string) => void
  onDelete: (roomId: string, roomName: string) => void
}

function RoomTreeBranch(props: RoomTreeBranchProps) {
  const { nodes, ...rest } = props
  return (
    <>
      {nodes.map(node => (
        <RoomTreeNodeView key={node.room.id} node={node} {...rest} />
      ))}
    </>
  )
}

function RoomTreeNodeView({
  node,
  selectedRoom,
  onGo,
  onPin,
  isAdmin,
  onRename,
  onDelete,
}: Omit<RoomTreeBranchProps, 'nodes' | 'projectId'> & { node: RoomTreeNode }) {
  const { t } = useLocale()
  // Cap the padding so that at depth >= 4 the label stays visible.
  // Deep threads are rare in practice and the user can still use
  // the room header's parent breadcrumb for navigation.
  const indentPx = Math.min(node.depth, 4) * 12

  const isSelected = selectedRoom === node.room.id
  // Pin action is only offered for top-level rooms — sub-rooms are
  // contextually tied to their parent and shouldn't float out of
  // the tree (#47 scope).
  const canPin = node.depth === 0

  return (
    <>
      <div
        className={`group relative flex w-full items-center rounded-[var(--radius-sm)] mb-0.5 ${
          isSelected
            ? 'bg-[var(--color-surface)] shadow-whisper'
            : 'hover:bg-[var(--color-surface-hover)]'
        }`}
      >
        <button
          onClick={() => onGo(`/rooms/${node.room.id}`)}
          style={{ paddingLeft: `${indentPx + 8}px` }}
          className={`flex min-h-11 min-w-0 flex-1 items-center pr-2 text-[14px] font-medium transition-colors ${
            isSelected
              ? 'text-[var(--color-foreground)]'
              : 'text-[var(--color-foreground-muted)] group-hover:text-[var(--color-foreground)]'
          }`}
          data-testid={`sidebar-room-${node.room.id}`}
        >
          <Hash className="mr-1.5 h-3.5 w-3.5 shrink-0 text-[var(--color-foreground-subtle)]" />
          <span className="min-w-0 truncate">{node.room.name}</span>
          {node.room.has_updates && <UpdateDot className="ml-auto" />}
        </button>
        {canPin && (
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); onPin(node.room.id) }}
            title={t('chat.pinToTop')}
            aria-label={t('chat.pinRoom', { name: node.room.name })}
            data-testid={`sidebar-pin-${node.room.id}`}
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded text-[var(--color-foreground-subtle)] opacity-100 transition-opacity hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-foreground)] md:h-6 md:w-6 md:opacity-0 md:group-hover:opacity-100"
          >
            <Pin className="h-3 w-3" />
          </button>
        )}
        {isAdmin && (
          <SidebarRoomMenu
            roomId={node.room.id}
            onRename={() => onRename(node.room.id)}
            onDelete={() => onDelete(node.room.id, node.room.name)}
          />
        )}
      </div>
      {node.children.length > 0 && (
        <RoomTreeBranch
          nodes={node.children}
          selectedRoom={selectedRoom}
          onGo={onGo}
          onPin={onPin}
          isAdmin={isAdmin}
          projectId=""
          onRename={onRename}
          onDelete={onDelete}
        />
      )}
    </>
  )
}

/**
 * Admin-only DM list variant (#71).
 *
 * Broken out so ``useAgents()`` — which hits an admin-gated
 * endpoint — only mounts for admins. The parent Sidebar conditionally
 * renders this vs the plain DM list based on ``user.is_admin``.
 *
 * Issue #105 — each admin row exposes the same ``AgentSettingsMenu``
 * that AdminMachines uses (Edit avatar / Edit manifest / Manage
 * rooms / Activity / Copy ID / Delete). Handlers and dialog state
 * are duplicated from AdminMachines rather than shared via a hook:
 * the two call sites differ in their post-delete refetch (AdminMachines
 * refreshes its machine detail, sidebar refreshes the DM list) and
 * coupling them would force one site's ``onChange`` shape onto the
 * other. See plan §3.2 decision 2 for the full rationale.
 */
// #237 — user-scoped localStorage for per-agent DM tree expansion.
// Pattern borrowed from #234's topology layout hook — same try/catch
// shield so Safari private mode doesn't blow up the sidebar.
function loadExpandedAgents(userId: string | undefined): Set<string> {
  if (!userId) return new Set()
  try {
    const raw = localStorage.getItem(`anygarden_expanded_agents_v1_${userId}`)
    if (!raw) return new Set()
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return new Set()
    return new Set(parsed.filter((v): v is string => typeof v === 'string'))
  } catch {
    return new Set()
  }
}

function saveExpandedAgents(
  userId: string | undefined,
  expanded: Set<string>,
): void {
  if (!userId) return
  try {
    localStorage.setItem(
      `anygarden_expanded_agents_v1_${userId}`,
      JSON.stringify([...expanded]),
    )
  } catch {
    /* private-mode: swallow */
  }
}

function AgentDMListAdmin({
  dms,
  selectedRoom,
  onGo,
}: {
  dms: Room[]
  selectedRoom: string | null
  onGo: (path: string) => void
}) {
  const { t } = useLocale()
  const { confirm, notify } = useFeedback()
  const {
    agents,
    deleteAgent,
    updateAgent,
    fetchAgentFiles,
    upsertAgentFile,
    deleteAgentFile,
    fetchAttachedSkills,
    fetchSkillPreview,
    fetchEngineCatalog,
  } = useAgents()
  const { fetchAgentDMs, createAgentDM } = useRooms()
  const { user } = useAuth()
  const userId = user?.id

  // #237 — track which agent rows have their DM tree expanded.
  // Populated lazily from localStorage on mount so the state persists
  // across page reloads per-user (mirroring #234 topology pattern).
  const [expandedAgents, setExpandedAgents] = useState<Set<string>>(
    () => loadExpandedAgents(userId),
  )
  useEffect(() => {
    setExpandedAgents(loadExpandedAgents(userId))
  }, [userId])

  const toggleExpanded = useCallback(
    (agentId: string) => {
      setExpandedAgents(prev => {
        const next = new Set(prev)
        if (next.has(agentId)) next.delete(agentId)
        else next.add(agentId)
        saveExpandedAgents(userId, next)
        return next
      })
    },
    [userId],
  )

  const handleCreateDM = useCallback(
    async (agentId: string) => {
      try {
        const room = await createAgentDM(agentId)
        // Auto-expand the agent tree when a new DM is added so the
        // user sees the freshly created row without a second click.
        setExpandedAgents(prev => {
          const next = new Set(prev)
          next.add(agentId)
          saveExpandedAgents(userId, next)
          return next
        })
        onGo(`/rooms/${room.id}`)
      } catch (e) {
        console.warn('createAgentDM failed', e)
      }
    },
    [createAgentDM, onGo, userId],
  )

  // Per-row dialogs — mirrors AdminMachines.tsx state shape so the
  // two routes never disagree on dialog wiring.
  //
  // #158 — collapsed into a single settings dialog. #281 — store only
  // the open agent's ID; derive the Agent object from the live
  // ``agents`` list each render so an in-dialog edit (which triggers
  // ``updateAgent → fetchAgents``) is reflected immediately. The
  // earlier snapshot-based ``useState<Agent | null>`` left the dialog
  // showing stale model/reasoning/collaboration values until close-
  // and-reopen. See AgentSettingsDialog.test.tsx — "parent state
  // pattern (#281)" for the regression test.
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settingsAgentId, setSettingsAgentId] = useState<string | null>(null)
  const settingsAgent = useMemo<Agent | null>(
    () =>
      settingsAgentId
        ? agents.find(a => a.id === settingsAgentId) ?? null
        : null,
    [agents, settingsAgentId],
  )
  // #241 — DM rename dialog. ``editDMRoomId`` mirrors the project
  // rooms ``editRoomId`` state in the Sidebar root; kept local here
  // because AgentDMListAdmin owns the DM tree and the root doesn't
  // thread its dialog state into this sub-tree.
  const [editDMRoomId, setEditDMRoomId] = useState<string | null>(null)

  const handleDeleteDM = useCallback(
    async (roomId: string, displayName: string) => {
      const ok = await confirm({
        title: t('chat.deleteConversationTitle', { name: displayName }),
        description: t('chat.deleteConversationDescription'),
        confirmLabel: t('chat.delete'),
        destructive: true,
      })
      if (!ok) return
      const resp = await apiFetch(`/api/v1/rooms/${roomId}`, {
        method: 'DELETE',
      })
      if (resp.status === 204) {
        // Refresh the DM list immediately; the WS ``room_deleted``
        // broadcast will also fan out, but this keeps the acting
        // user's UI snappy without waiting on the round-trip.
        fetchAgentDMs()
        // Navigate away if the deleted DM was the currently open one.
        if (selectedRoom === roomId) onGo('/')
        return
      }
      let detail = t('chat.failedDeleteConversation', { status: resp.status })
      try {
        const body = await resp.json()
        if (body && typeof body.detail === 'string') detail = body.detail
      } catch {
        /* ignore */
      }
      notify({ message: detail, tone: 'error' })
    },
    [fetchAgentDMs, onGo, selectedRoom, t, confirm, notify],
  )

  const handleOpenSettings = (agentId: string) => {
    if (!agents.some(a => a.id === agentId)) return
    setSettingsAgentId(agentId)
    setSettingsOpen(true)
  }

  const handleDeleteAgent = async (agentId: string): Promise<boolean> => {
    if (!await confirm({
      title: t('chat.deleteAgentTitle'),
      description: t('chat.deleteAgentDescription'),
      confirmLabel: t('chat.delete'),
      destructive: true,
    })) return false
    await deleteAgent(agentId)
    // Delete cascades the DM room server-side. Refresh the sidebar
    // DM list so the row disappears immediately instead of lingering
    // until the next WS invalidate.
    fetchAgentDMs()
    return true
  }

  // #148 Part 2 — flip agent-side ambient opt-out. ``updateAgent``
  // already calls ``fetchAgents`` on success, so the next render
  // reads the fresh flag and the check mark updates without any
  // extra wiring here.
  const handleToggleContextWindowOptOut = async (
    agentId: string,
    current: boolean,
  ) => {
    try {
      await updateAgent(agentId, {
        context_window_opt_out: !current,
        context_window_opt_out_set: true,
      })
    } catch {
      // The Sidebar has no top-level error banner; swallowing keeps
      // the DM list quiet. The admin can retry by clicking again.
    }
  }

  // #237 — group DMs by agent. Each agent's DM list is sorted by
  // name so the ordering stays stable across re-renders. Orphan
  // DMs (where ``findAgentForDM`` can't resolve a matching agent
  // row) fall into a separate "unowned" bucket rendered at the end.
  const grouped = useMemo(() => {
    const byAgent = new Map<string, { agent: Agent; dms: Room[] }>()
    const orphans: Room[] = []
    for (const dm of dms) {
      const agent = findAgentForDM(dm, agents)
      if (!agent) {
        orphans.push(dm)
        continue
      }
      const bucket = byAgent.get(agent.id)
      if (bucket) bucket.dms.push(dm)
      else byAgent.set(agent.id, { agent, dms: [dm] })
    }
    for (const { dms: list } of byAgent.values()) {
      list.sort((a, b) => a.name.localeCompare(b.name))
    }
    return { byAgent, orphans }
  }, [dms, agents])

  return (
    <div className="flex flex-col gap-0.5">
      {[...grouped.byAgent.values()].map(({ agent, dms: agentDms }) => {
        const machineOffline = agent.machine_online === false
        const online = deriveAgentOnline(agent.actual_state, { machineOffline })
        const displayState = agentStatusLabel(agent.actual_state, { machineOffline })
        // Adaptive: single-DM agents render inline without a toggle
        // chevron (plan §3.2 decision 5). Two+ DMs render a collapsible
        // tree with the DM list underneath.
        const hasMultipleDMs = agentDms.length > 1
        const agentHasUpdates = hasMultipleDMs && agentDms.some(dm => dm.has_updates)
        const isExpanded = !hasMultipleDMs || expandedAgents.has(agent.id)
        // For single-DM agents the row itself navigates to the DM so
        // the clickable area feels identical to the pre-#237 behaviour.
        // For multi-DM agents the row toggles expansion and the DM
        // children are the click targets.
        const soloDM = !hasMultipleDMs ? agentDms[0] : null
        const soloIsSelected = soloDM ? selectedRoom === soloDM.id : false
        return (
          <div key={agent.id} className="flex flex-col">
            <div
              className={`group relative flex w-full items-center rounded-[var(--radius-sm)] transition-colors ${
                soloIsSelected
                  ? 'bg-[var(--color-surface)] shadow-whisper'
                  : 'hover:bg-[var(--color-surface-hover)]'
              }`}
            >
              <button
                onClick={() => {
                  if (soloDM) onGo(`/rooms/${soloDM.id}`)
                  else toggleExpanded(agent.id)
                }}
                data-testid={
                  soloDM
                    ? `sidebar-dm-${soloDM.id}`
                    : `sidebar-agent-${agent.id}`
                }
                className={`flex min-h-11 min-w-0 flex-1 items-center gap-2 px-2 py-1.5 text-[14px] font-medium transition-colors ${
                  soloIsSelected
                    ? 'text-[var(--color-foreground)]'
                    : 'text-[var(--color-foreground-muted)] group-hover:text-[var(--color-foreground)]'
                }`}
              >
                {hasMultipleDMs ? (
                  isExpanded ? (
                    <ChevronDown className="h-3 w-3 shrink-0" />
                  ) : (
                    <ChevronRight className="h-3 w-3 shrink-0" />
                  )
                ) : null}
                <EntityAvatar
                  id={agent.id}
                  name={agent.name}
                  kind="agent"
                  engine={agent.engine}
                  size="xs"
                  avatarKind={
                    (agent.avatar_kind as AvatarKind | null | undefined) ?? null
                  }
                  avatarValue={agent.avatar_value ?? null}
                />
                <PresenceDot
                  variant="agent"
                  online={online}
                  agentState={displayState}
                />
                <span className="min-w-0 truncate" title={agent.name}>
                  {agent.name}
                </span>
                {soloDM?.has_updates && <UpdateDot className="ml-auto" />}
                {hasMultipleDMs && (
                  // #243 — hide the DM count on hover so the space is
                  // handed back to the name while the action buttons
                  // (``+`` new DM + ``⋯`` settings) are revealed. On
                  // mouse-out the badge fades back in; the information
                  // is never lost, only toggled with the user's intent.
                  <span className="ml-auto flex shrink-0 items-center gap-1.5 group-hover:hidden">
                    {agentHasUpdates && <UpdateDot />}
                    <span className="rounded-full bg-black/5 px-1.5 text-[11px] text-[var(--color-foreground-muted)]">
                      {agentDms.length}
                    </span>
                  </span>
                )}
              </button>
              <span
                // #243 — ``inline-flex`` is load-bearing: without it
                // ``AgentSettingsMenu``'s wrapping ``<div class="relative">``
                // drops onto its own line (block-in-inline quirk),
                // making the agent row appear twice as tall. Pairing
                // with ``items-center gap-0.5`` keeps the ``+`` and
                // ``⋯`` buttons side-by-side at 24×24 each.
                className="mr-1 inline-flex shrink-0 items-center gap-0.5 opacity-100 md:opacity-0 md:group-hover:opacity-100 has-[[aria-expanded=true]]:opacity-100 transition-opacity"
                data-testid={`sidebar-agent-actions-${agent.id}`}
              >
                <button
                  onClick={(e) => {
                    e.stopPropagation()
                    void handleCreateDM(agent.id)
                  }}
                  title={t('chat.newConversation')}
                  aria-label={t('chat.newConversation')}
                  className="inline-flex h-11 w-11 items-center justify-center rounded-[var(--radius-sm)] text-[var(--color-foreground-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-foreground)] md:h-6 md:w-6"
                  data-testid={`sidebar-new-dm-${agent.id}`}
                >
                  <Plus className="h-3.5 w-3.5" />
                </button>
                <AgentSettingsMenu
                  compact
                  onOpenSettings={() => handleOpenSettings(agent.id)}
                  onDelete={() => { void handleDeleteAgent(agent.id) }}
                  contextWindowOptOut={
                    agent.context_window_opt_out ?? false
                  }
                  onToggleContextWindowOptOut={() =>
                    handleToggleContextWindowOptOut(
                      agent.id,
                      agent.context_window_opt_out ?? false,
                    )
                  }
                />
              </span>
            </div>
            {hasMultipleDMs && isExpanded && (
              <div className="ml-5 flex flex-col gap-0.5 border-l border-[var(--color-border-subtle)] pl-1.5">
                {agentDms.map(dm => {
                  const isSel = selectedRoom === dm.id
                  const label = dm.name.replace(/^DM:\s*/, '')
                  return (
                    <div
                      key={dm.id}
                      className={`group relative flex min-w-0 items-center rounded-[var(--radius-sm)] ${
                        isSel
                          ? 'bg-[var(--color-surface)] shadow-whisper text-[var(--color-foreground)]'
                          : 'text-[var(--color-foreground-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-foreground)]'
                      }`}
                    >
                      <button
                        onClick={() => onGo(`/rooms/${dm.id}`)}
                        data-testid={`sidebar-dm-${dm.id}`}
                        className="flex min-h-11 min-w-0 flex-1 items-center gap-2 px-2 py-1 text-[13px] font-medium transition-colors"
                      >
                        <MessageSquare className="h-3 w-3 shrink-0" />
                        <span className="min-w-0 truncate">{label}</span>
                        {dm.ephemeral && (
                          <span
                            className="shrink-0 rounded-full bg-black/5 px-1.5 text-[10px] text-[var(--color-foreground-muted)]"
                            title={t('chat.temporarySession')}
                          >
                            {t('chat.temporaryShort')}
                          </span>
                        )}
                        {dm.has_updates && <UpdateDot className="ml-auto" />}
                      </button>
                      <SidebarRoomMenu
                        roomId={dm.id}
                        onRename={() => setEditDMRoomId(dm.id)}
                        onDelete={() => { void handleDeleteDM(dm.id, label) }}
                      />
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )
      })}
      {grouped.orphans.map(dm => {
        const isSel = selectedRoom === dm.id
        const label = dm.name.replace(/^DM:\s*/, '')
        return (
          <button
            key={dm.id}
            onClick={() => onGo(`/rooms/${dm.id}`)}
            data-testid={`sidebar-dm-${dm.id}`}
            className={`flex min-h-11 items-center gap-2 rounded-[var(--radius-sm)] px-2 py-1.5 text-[14px] font-medium transition-colors ${
              isSel
                ? 'bg-[var(--color-surface)] shadow-whisper text-[var(--color-foreground)]'
                : 'text-[var(--color-foreground-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-foreground)]'
            }`}
          >
            <EntityAvatar
              id={dm.representative_agent_id ?? dm.id}
              name={label}
              kind="agent"
              size="xs"
            />
            <span className="min-w-0 truncate">{label}</span>
            {dm.has_updates && <UpdateDot className="ml-auto" />}
          </button>
        )
      })}

      {/* #158 — single unified settings dialog replaces the four
          per-agent dialogs (edit manifest / rooms / history / avatar).
          Mounted inside ``AgentDMListAdmin`` (not the Sidebar root) so
          the non-admin render path stays free of this state. Both
          AdminMachines and the sidebar mount their own instances; the
          two routes never coexist so duplicate instances don't race. */}
      <AgentSettingsDialog
        agent={settingsAgent}
        open={settingsOpen}
        onOpenChange={open => {
          setSettingsOpen(open)
          // #281 — drop the tracked ID on close so an externally-
          // deleted agent doesn't leave the dialog re-opening into a
          // ``null`` derived prop on the next admin click.
          if (!open) setSettingsAgentId(null)
        }}
        fetchAgentFiles={fetchAgentFiles}
        updateAgent={updateAgent}
        upsertAgentFile={upsertAgentFile}
        deleteAgentFile={deleteAgentFile}
        fetchAttachedSkills={fetchAttachedSkills}
        fetchSkillPreview={fetchSkillPreview}
        fetchEngineCatalog={fetchEngineCatalog}
        onRoomsChange={() => { fetchAgentDMs() }}
        onDelete={
          settingsAgent
            ? async () => {
                if (await handleDeleteAgent(settingsAgent.id)) {
                  setSettingsOpen(false)
                  setSettingsAgentId(null)
                }
              }
            : undefined
        }
        contextWindowOptOut={settingsAgent?.context_window_opt_out ?? false}
        onToggleContextWindowOptOut={
          settingsAgent
            ? () =>
                handleToggleContextWindowOptOut(
                  settingsAgent.id,
                  settingsAgent.context_window_opt_out ?? false,
                )
            : undefined
        }
      />
      {/* #241 — DM rename dialog. Reuses RoomEditDialog so DM
          rename flows through the same PATCH /rooms/{id} endpoint
          as project rooms. ``onSaved`` refetches the DM list so
          the new name shows up immediately. */}
      {editDMRoomId && (
        <RoomEditDialog
          roomId={editDMRoomId}
          open={editDMRoomId !== null}
          onOpenChange={(o) => { if (!o) setEditDMRoomId(null) }}
          onSaved={() => { fetchAgentDMs() }}
        />
      )}
    </div>
  )
}

function PinnedRoomItem({
  room,
  selectedRoom,
  onGo,
  onUnpin,
}: {
  room: Room
  selectedRoom: string | null
  onGo: (path: string) => void
  onUnpin: () => void
}) {
  const { t } = useLocale()
  const isSelected = selectedRoom === room.id
  // ``useSortable`` wires each item into the parent
  // ``SortableContext``. ``attributes`` + ``listeners`` go on the
  // drag handle so the row itself stays click-to-navigate; the
  // handle is the only DnD trigger. ``transform`` and
  // ``transition`` animate the reorder smoothly.
  const {
    attributes, listeners, setNodeRef, transform, transition, isDragging,
  } = useSortable({ id: room.id })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  }

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`group relative flex w-full items-center rounded-[var(--radius-sm)] ${
        isSelected
          ? 'bg-[var(--color-surface)] shadow-whisper'
          : 'hover:bg-[var(--color-surface-hover)]'
      }`}
    >
      <button
        type="button"
        {...attributes}
        {...listeners}
        title={t('chat.dragToReorder')}
        aria-label={t('chat.reorderRoom', { name: room.name })}
        data-testid={`sidebar-drag-${room.id}`}
        className="flex h-11 w-11 cursor-grab touch-none items-center justify-center rounded text-[var(--color-foreground-subtle)] opacity-100 hover:bg-[var(--color-surface-hover)] focus:opacity-100 active:cursor-grabbing md:h-6 md:w-6 md:opacity-0 md:group-hover:opacity-100"
      >
        <GripVertical className="h-3 w-3" />
      </button>
      <button
        onClick={() => onGo(`/rooms/${room.id}`)}
        className={`flex min-h-11 min-w-0 flex-1 items-center py-1 pr-2 text-[14px] font-medium transition-colors ${
          isSelected
            ? 'text-[var(--color-foreground)]'
            : 'text-[var(--color-foreground-muted)] group-hover:text-[var(--color-foreground)]'
        }`}
        data-testid={`sidebar-pinned-${room.id}`}
      >
        <Hash className="mr-1.5 h-3.5 w-3.5 shrink-0 text-[var(--color-foreground-subtle)]" />
        <span className="min-w-0 truncate">{room.name}</span>
        {room.has_updates && <UpdateDot className="ml-auto" />}
      </button>
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); onUnpin() }}
        title={t('chat.unpin')}
        aria-label={t('chat.unpinRoom', { name: room.name })}
        data-testid={`sidebar-unpin-${room.id}`}
        className="mr-1 flex h-11 w-11 items-center justify-center rounded text-[var(--color-foreground-subtle)] opacity-100 hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-foreground)] transition-opacity md:h-6 md:w-6 md:opacity-0 md:group-hover:opacity-100"
      >
        <PinOff className="h-3 w-3" />
      </button>
    </div>
  )
}

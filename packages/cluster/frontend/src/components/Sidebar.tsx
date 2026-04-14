import { useState, useEffect, useMemo } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { useAuth } from '@/hooks/useAuth'
import { useRooms, type Room } from '@/hooks/useRooms'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Input } from '@/components/ui/input'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogTrigger,
} from '@/components/ui/dialog'
import {
  Hash, Plus, ChevronDown, ChevronRight, LogOut, Bot, Server, MessageSquare, X,
} from 'lucide-react'

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

interface SidebarProps {
  selectedRoom: string | null
  /** Mobile off-canvas open state. Desktop (md+) is always visible. */
  open?: boolean
  onClose?: () => void
}

export default function Sidebar({ selectedRoom, open = false, onClose }: SidebarProps) {
  const { user, logout } = useAuth()
  const { projects, rooms, agentDMs, createProject, createRoom } = useRooms()
  const navigate = useNavigate()
  const location = useLocation()

  const [expandedProjects, setExpandedProjects] = useState<Set<string>>(() => {
    try {
      const saved = localStorage.getItem('doorae_expanded_projects')
      return saved ? new Set(JSON.parse(saved)) : new Set()
    } catch { return new Set() }
  })

  useEffect(() => {
    try {
      localStorage.setItem(
        'doorae_expanded_projects',
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
  // ``RoomTreeBranch``.
  const projectTrees = useMemo(() => {
    const out: Record<string, RoomTreeNode[]> = {}
    for (const projectId of Object.keys(rooms)) {
      out[projectId] = buildRoomTree(rooms[projectId] ?? [])
    }
    return out
  }, [rooms])

  const handleCreateProject = async () => {
    if (!newProjectName.trim()) return
    try {
      await createProject(newProjectName.trim())
      setNewProjectName('')
      setProjectDialogOpen(false)
    } catch { /* ignore */ }
  }

  const handleCreateRoom = async () => {
    if (!newRoomName.trim() || !roomProjectId) return
    try {
      await createRoom(roomProjectId, newRoomName.trim())
      setNewRoomName('')
      setRoomDialogOpen(false)
    } catch { /* ignore */ }
  }

  const openNewRoomDialog = (projectId: string) => {
    setRoomProjectId(projectId)
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
          aria-label="Close sidebar"
          className="fixed inset-0 z-30 bg-black/25 backdrop-blur-[1px] md:hidden"
          onClick={onClose}
        />
      )}

      <aside
        className={`
          fixed inset-y-0 left-0 z-40 flex h-full w-64 flex-col border-r border-[var(--color-border)] bg-[var(--color-surface-alt)]
          transform transition-transform duration-200 ease-out
          md:static md:z-auto md:translate-x-0
          ${open ? 'translate-x-0 shadow-deep' : '-translate-x-full'}
        `}
      >
      {/* Header */}
      <div className="flex h-14 items-center justify-between px-4">
        <div className="flex items-center">
          <MessageSquare className="mr-2 size-5 text-[var(--color-foreground)]" />
          <h1 className="text-[15px] font-bold text-[var(--color-foreground)] tracking-tight">Doorae</h1>
        </div>
        <button
          type="button"
          className="md:hidden rounded-[var(--radius-sm)] p-1 text-[var(--color-foreground-muted)] hover:bg-black/5"
          onClick={onClose}
          aria-label="Close sidebar"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Projects & Rooms */}
      <ScrollArea className="flex-1">
        <div className="px-2 py-2">
          {projects.map(project => (
            <div key={project.id} className="mb-1">
              <button
                onClick={() => toggleProject(project.id)}
                className="text-nav flex w-full items-center rounded-[var(--radius-sm)] px-2 py-1.5 text-[var(--color-foreground)] hover:bg-black/5 transition-colors"
              >
                {expandedProjects.has(project.id)
                  ? <ChevronDown className="mr-1 h-4 w-4 shrink-0 text-[var(--color-foreground-subtle)]" />
                  : <ChevronRight className="mr-1 h-4 w-4 shrink-0 text-[var(--color-foreground-subtle)]" />
                }
                <span className="truncate">{project.name}</span>
              </button>

              {expandedProjects.has(project.id) && (
                <div className="ml-3 mt-0.5 flex flex-col gap-0.5">
                  <RoomTreeBranch
                    nodes={projectTrees[project.id] ?? []}
                    selectedRoom={selectedRoom}
                    onGo={go}
                  />

                  <button
                    onClick={() => openNewRoomDialog(project.id)}
                    className="flex w-full items-center rounded-[var(--radius-sm)] px-2 py-1 text-[14px] font-medium text-[var(--color-foreground-muted)] hover:bg-black/5 hover:text-[var(--color-foreground)] transition-colors"
                  >
                    <Plus className="mr-1.5 h-3.5 w-3.5 shrink-0 text-[var(--color-foreground-subtle)]" />
                    <span>New Room</span>
                  </button>
                </div>
              )}
            </div>
          ))}

          {projects.length === 0 && (
            <p className="text-caption px-2 py-4 text-center">
              No projects yet
            </p>
          )}
        </div>
      </ScrollArea>

      {/* New Project button */}
      <div className="p-2">
        <Dialog open={projectDialogOpen} onOpenChange={setProjectDialogOpen}>
          <DialogTrigger asChild>
            <Button variant="ghost" size="sm" className="w-full justify-start text-[var(--color-foreground-muted)]">
              <Plus className="mr-2 h-4 w-4" />
              New Project
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Create Project</DialogTitle>
            </DialogHeader>
            <div className="space-y-4 py-2">
              <Input
                placeholder="Project name"
                value={newProjectName}
                onChange={e => setNewProjectName(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && handleCreateProject()}
              />
            </div>
            <DialogFooter>
              <Button onClick={handleCreateProject} disabled={!newProjectName.trim()}>
                Create
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>

      {/* New Room dialog */}
      <Dialog open={roomDialogOpen} onOpenChange={setRoomDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create Room</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <Input
              placeholder="Room name"
              value={newRoomName}
              onChange={e => setNewRoomName(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleCreateRoom()}
            />
          </div>
          <DialogFooter>
            <Button onClick={handleCreateRoom} disabled={!newRoomName.trim()}>
              Create
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Agents DM section */}
      {agentDMs.length > 0 && (
        <div className="border-t border-[var(--color-border)] px-2 py-2">
          <button
            onClick={() => setAgentsExpanded(prev => !prev)}
            className="flex w-full items-center gap-1 px-2 py-1 text-badge uppercase text-[var(--color-foreground-muted)] hover:text-[var(--color-foreground)] transition-colors"
          >
            {agentsExpanded
              ? <ChevronDown className="h-3 w-3" />
              : <ChevronRight className="h-3 w-3" />}
            Agents
          </button>
          {agentsExpanded && (
            <div className="flex flex-col gap-0.5">
              {agentDMs.map(dm => (
                <button
                  key={dm.id}
                  onClick={() => go(`/rooms/${dm.id}`)}
                  data-testid={`sidebar-dm-${dm.id}`}
                  className={`flex w-full items-center rounded-[var(--radius-sm)] px-2 py-1.5 text-[14px] font-medium transition-colors ${
                    selectedRoom === dm.id
                      ? 'bg-white shadow-whisper text-[var(--color-foreground)]'
                      : 'text-[var(--color-foreground-muted)] hover:bg-black/5 hover:text-[var(--color-foreground)]'
                  }`}
                >
                  <Bot className="mr-2 h-4 w-4 text-[var(--color-foreground-subtle)]" />
                  {dm.name.replace(/^DM:\s*/, '')}
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Admin section */}
      {user?.is_admin && (
        <div className="border-t border-[var(--color-border)] px-2 py-2">
          <p className="text-badge uppercase px-2 py-1 text-[var(--color-foreground-muted)]">
            Admin
          </p>
          <div className="flex flex-col gap-0.5">
            <button
              onClick={() => go('/admin/agents')}
              className={`flex w-full items-center rounded-[var(--radius-sm)] px-2 py-1.5 text-[14px] font-medium transition-colors ${
                location.pathname === '/admin/agents'
                  ? 'bg-white shadow-whisper text-[var(--color-foreground)]'
                  : 'text-[var(--color-foreground-muted)] hover:bg-black/5 hover:text-[var(--color-foreground)]'
              }`}
            >
              <Bot className="mr-2 h-4 w-4 text-[var(--color-foreground-subtle)]" />
              Agents
            </button>
            <button
              onClick={() => go('/admin/machines')}
              className={`flex w-full items-center rounded-[var(--radius-sm)] px-2 py-1.5 text-[14px] font-medium transition-colors ${
                location.pathname === '/admin/machines'
                  ? 'bg-white shadow-whisper text-[var(--color-foreground)]'
                  : 'text-[var(--color-foreground-muted)] hover:bg-black/5 hover:text-[var(--color-foreground)]'
              }`}
            >
              <Server className="mr-2 h-4 w-4 text-[var(--color-foreground-subtle)]" />
              Machines
            </button>
          </div>
        </div>
      )}

      {/* User info */}
      <div className="flex items-center justify-between gap-2 border-t border-[var(--color-border)] px-3 py-3">
        <span className="truncate text-xs text-[var(--color-foreground-muted)]">{user?.email}</span>
        <Button variant="ghost" size="icon" onClick={logout} title="Logout">
          <LogOut className="h-4 w-4" />
        </Button>
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
function RoomTreeBranch({
  nodes,
  selectedRoom,
  onGo,
}: {
  nodes: RoomTreeNode[]
  selectedRoom: string | null
  onGo: (path: string) => void
}) {
  return (
    <>
      {nodes.map(node => (
        <RoomTreeNodeView
          key={node.room.id}
          node={node}
          selectedRoom={selectedRoom}
          onGo={onGo}
        />
      ))}
    </>
  )
}

function RoomTreeNodeView({
  node,
  selectedRoom,
  onGo,
}: {
  node: RoomTreeNode
  selectedRoom: string | null
  onGo: (path: string) => void
}) {
  // Cap the padding so that at depth >= 4 the label stays visible.
  // Deep threads are rare in practice and the user can still use
  // the room header's parent breadcrumb for navigation.
  const indentPx = Math.min(node.depth, 4) * 12

  const isSelected = selectedRoom === node.room.id

  return (
    <>
      <button
        onClick={() => onGo(`/rooms/${node.room.id}`)}
        style={{ paddingLeft: `${indentPx + 8}px` }}
        className={`flex w-full items-center rounded-[var(--radius-sm)] py-1 pr-2 text-[14px] font-medium transition-colors mb-0.5 ${
          isSelected
            ? 'bg-white shadow-whisper text-[var(--color-foreground)]'
            : 'text-[var(--color-foreground-muted)] hover:bg-black/5 hover:text-[var(--color-foreground)]'
        }`}
        data-testid={`sidebar-room-${node.room.id}`}
      >
        <Hash className="mr-1.5 h-3.5 w-3.5 shrink-0 text-[var(--color-foreground-subtle)]" />
        <span className="truncate">{node.room.name}</span>
      </button>
      {node.children.length > 0 && (
        <RoomTreeBranch
          nodes={node.children}
          selectedRoom={selectedRoom}
          onGo={onGo}
        />
      )}
    </>
  )
}

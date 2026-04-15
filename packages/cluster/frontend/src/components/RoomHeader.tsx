import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Hash, Users, UserPlus, Menu, ChevronLeft, FolderPlus, Settings, OctagonX, Crown, Link2 } from 'lucide-react'
import { useNavigate } from 'react-router-dom'

interface ParentBreadcrumb {
  id: string
  name: string
}

interface AgentParticipant {
  id: string
  agent_id: string
  display_name: string
}

interface RoomHeaderProps {
  roomName: string
  connected: boolean
  participantCount?: number
  parentBreadcrumb?: ParentBreadcrumb[]
  representativeAgentId?: string | null
  agentParticipants?: AgentParticipant[]
  onSetRepresentative?: (agentId: string | null) => void
  onManageAgents?: () => void
  onCreateSubRoom?: () => void
  onEditRoom?: () => void
  onManageInvites?: () => void
  onStopAllAgents?: () => void
  onOpenSidebar?: () => void
  onToggleParticipants?: () => void
}

export default function RoomHeader({
  roomName,
  connected,
  participantCount,
  parentBreadcrumb,
  representativeAgentId,
  agentParticipants,
  onSetRepresentative,
  onManageAgents,
  onCreateSubRoom,
  onEditRoom,
  onManageInvites,
  onStopAllAgents,
  onOpenSidebar,
  onToggleParticipants,
}: RoomHeaderProps) {
  const navigate = useNavigate()
  const hasParent = parentBreadcrumb && parentBreadcrumb.length > 0
  const immediateParent = hasParent
    ? parentBreadcrumb![parentBreadcrumb!.length - 1]
    : null

  return (
    <div className="flex h-14 items-center justify-between gap-2 border-b border-[var(--color-border)] bg-white px-4 md:px-6">
      <div className="flex min-w-0 items-center gap-2">
        {onOpenSidebar && (
          <Button
            variant="ghost"
            size="icon"
            onClick={onOpenSidebar}
            className="md:hidden"
            aria-label="Open sidebar"
          >
            <Menu className="h-5 w-5" />
          </Button>
        )}
        {immediateParent && (
          <button
            onClick={() => navigate(`/rooms/${immediateParent.id}`)}
            className="flex items-center gap-1 rounded-[var(--radius-sm)] px-1.5 py-0.5 text-xs text-[var(--color-foreground-muted)] hover:bg-black/5 hover:text-[var(--color-foreground)] transition-colors"
            title={`Back to ${immediateParent.name}`}
            data-testid="room-header-parent-link"
          >
            <ChevronLeft className="h-3.5 w-3.5" />
            <span className="hidden max-w-[140px] truncate sm:inline">
              {immediateParent.name}
            </span>
          </button>
        )}
        <Hash className="h-5 w-5 shrink-0 text-[var(--color-foreground-subtle)]" />
        <h2 className="text-card-title truncate text-[var(--color-foreground)]">{roomName}</h2>
      </div>
      <div className="flex shrink-0 items-center gap-2 md:gap-3">
        {participantCount !== undefined && (
          onToggleParticipants ? (
            // Click toggles the ParticipantListPopover that ChatPage /
            // GuestRoomPage mount next to the header. Visible on
            // mobile too — the participant list is the whole point
            // of this button and hiding it behind ``sm:`` made the
            // feature unreachable on phones.
            <button
              type="button"
              onClick={onToggleParticipants}
              // ``hover:bg-black/5 cursor-pointer`` matches the
              // project-wide ghost-button convention recorded in
              // docs/history/STATUS.md (PR #31/#32). Using the ghost
              // rule keeps the participant toggle visually aligned
              // with every other header control and gives it the
              // expected pointer/highlight affordance.
              className="text-caption flex items-center gap-1 rounded-[var(--radius-sm)] px-1.5 py-0.5 hover:bg-black/5 cursor-pointer"
              title="Show room participants"
              data-testid="room-header-participants-toggle"
            >
              <Users className="h-4 w-4" />
              <span>{participantCount}</span>
            </button>
          ) : (
            <div className="text-caption flex items-center gap-1">
              <Users className="h-4 w-4" />
              <span>{participantCount}</span>
            </div>
          )
        )}
        {onStopAllAgents && (
          <Button
            variant="ghost"
            size="sm"
            onClick={onStopAllAgents}
            title="Stop all agents in this room"
            className="text-red-600 hover:text-red-700 hover:bg-red-50"
          >
            <OctagonX className="mr-1 h-4 w-4" />
            <span className="hidden sm:inline">Stop All</span>
          </Button>
        )}
        {onCreateSubRoom && (
          <Button
            variant="ghost"
            size="sm"
            onClick={onCreateSubRoom}
            title="Create a sub-room under this room"
            data-testid="room-header-new-sub-room"
          >
            <FolderPlus className="mr-1 h-4 w-4" />
            <span className="hidden sm:inline">Sub-room</span>
          </Button>
        )}
        {onSetRepresentative && agentParticipants && agentParticipants.length > 0 && (
          <select
            value={representativeAgentId ?? ''}
            onChange={(e) => onSetRepresentative(e.target.value || null)}
            className="h-8 rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-white px-2 text-xs text-[var(--color-foreground)]"
            title="Set representative agent"
          >
            <option value="">No representative</option>
            {agentParticipants.map((ap) => (
              <option key={ap.agent_id} value={ap.agent_id}>
                {ap.display_name}
              </option>
            ))}
          </select>
        )}
        {onEditRoom && (
          <Button
            variant="ghost"
            size="sm"
            onClick={onEditRoom}
            title="Edit room name and description"
          >
            <Settings className="mr-1 h-4 w-4" />
            <span className="hidden sm:inline">Edit</span>
          </Button>
        )}
        {onManageAgents && (
          <Button
            variant="ghost"
            size="sm"
            onClick={onManageAgents}
            title="Manage agents in this room"
          >
            <UserPlus className="mr-1 h-4 w-4" />
            <span className="hidden sm:inline">Agents</span>
          </Button>
        )}
        {onManageInvites && (
          <Button
            variant="ghost"
            size="sm"
            onClick={onManageInvites}
            title="Manage guest invite links"
          >
            <Link2 className="mr-1 h-4 w-4" />
            <span className="hidden sm:inline">Invites</span>
          </Button>
        )}
        <Badge variant={connected ? 'default' : 'destructive'}>
          <span className="hidden sm:inline">{connected ? 'Connected' : 'Disconnected'}</span>
          <span className="sm:hidden">{connected ? '●' : '○'}</span>
        </Badge>
      </div>
    </div>
  )
}

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Hash, Users, Menu, ChevronLeft } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import RoomSettingsMenu from '@/components/RoomSettingsMenu'
import { EntityAvatar, type AvatarKind } from '@/components/EntityAvatar'

interface ParentBreadcrumb {
  id: string
  name: string
}

/**
 * Minimal shape for the agent whose identity the DM room carries.
 * Kept intentionally narrower than ``AgentParticipant`` because
 * non-admin users never receive the full admin-gated agent list —
 * ChatPage synthesizes this from the room's participants map, which
 * every viewer can see.
 */
interface DmAgent {
  id: string
  name: string
  /** Optional engine id (claude-code, codex, gemini-cli, …).
   *  When provided, shows up as a corner badge on the avatar. */
  engine?: string
  /** Issue #101 — optional avatar override forwarded from the
   *  participants map. Non-agent callers pass null/undefined and
   *  the avatar falls back to initials. */
  avatar_kind?: string | null
  avatar_value?: string | null
}

interface AgentParticipant {
  id: string
  agent_id: string
  display_name: string
  /** #54 — surfaced so the representative dropdown can append
   *  "(offline)" for agents that don't currently have a WS
   *  subscription. Optional: legacy callers that omit it keep
   *  working; the label simply reads the bare name. */
  online?: boolean
}

interface RoomHeaderProps {
  roomName: string
  connected: boolean
  participantCount?: number
  /** #54 — "n/N agents online". Rendered next to the Connected
   *  badge when both are supplied. ``agentsOnline`` can exceed
   *  ``agentsTotal`` briefly during reconnects; we clamp on display. */
  agentsOnline?: number
  agentsTotal?: number
  parentBreadcrumb?: ParentBreadcrumb[]
  representativeAgentId?: string | null
  agentParticipants?: AgentParticipant[]
  /** True when the current room is a 1:1 DM with an agent. Drives
   *  the left-glyph swap from #-hash to an engine avatar. */
  isDm?: boolean
  /** The agent whose identity the DM carries. Only consulted when
   *  ``isDm`` is true. */
  dmAgent?: DmAgent
  onSetRepresentative?: (agentId: string | null) => void
  onManageAgents?: () => void
  onCreateSubRoom?: () => void
  onEditRoom?: () => void
  onManageInvites?: () => void
  onStopAllAgents?: () => void
  onDeleteRoom?: () => void
  onOpenSidebar?: () => void
  onToggleParticipants?: () => void
}

/**
 * Room header.
 *
 * Layout is split into three zones:
 * - Left: breadcrumb + room name. Always visible.
 * - Right-status: participant count (toggles the list popover) and
 *   the connection badge. Always visible so a user can tell at a
 *   glance "who's here" and "am I connected" without a click.
 * - Right-controls: representative agent select (when available —
 *   it doubles as a read-out of the current representative) and a
 *   single ``…`` overflow menu that holds the admin mutation
 *   actions: Sub-room / Edit / Invites / Manage agents / Stop All.
 *
 * The overflow menu replaces the five inline icon-buttons we had
 * before. See ``RoomSettingsMenu`` for the grouping rationale — in
 * short, the header was getting crowded and destructive actions
 * (Stop All) benefit from sitting one click deeper.
 */

export default function RoomHeader({
  roomName,
  connected,
  participantCount,
  agentsOnline,
  agentsTotal,
  parentBreadcrumb,
  representativeAgentId,
  agentParticipants,
  isDm,
  dmAgent,
  onSetRepresentative,
  onManageAgents,
  onCreateSubRoom,
  onEditRoom,
  onManageInvites,
  onStopAllAgents,
  onDeleteRoom,
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
        {isDm && dmAgent ? (
          <EntityAvatar
            id={dmAgent.id}
            name={dmAgent.name}
            kind="agent"
            engine={dmAgent.engine}
            size="md"
            avatarKind={
              (dmAgent.avatar_kind as AvatarKind | null | undefined) ?? null
            }
            avatarValue={dmAgent.avatar_value ?? null}
            data-testid="room-header-dm-avatar"
          />
        ) : (
          <Hash className="h-5 w-5 shrink-0 text-[var(--color-foreground-subtle)]" />
        )}
        <h2 className="text-card-title truncate text-[var(--color-foreground)]">{roomName}</h2>
      </div>
      <div className="flex shrink-0 items-center gap-2 md:gap-3">
        {participantCount !== undefined && (
          onToggleParticipants ? (
            <button
              type="button"
              onClick={onToggleParticipants}
              // ``hover:bg-black/5 cursor-pointer`` matches the
              // project-wide ghost-button convention recorded in
              // docs/history/STATUS.md (PR #31/#32).
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
        {/* Representative agent stays inline — it's a combined
            read-out + control, and users scanning the header want
            to know the current representative without opening a
            menu. */}
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
                {ap.online === false ? ' (offline)' : ''}
              </option>
            ))}
          </select>
        )}
        <Badge variant={connected ? 'default' : 'destructive'}>
          <span className="hidden sm:inline">{connected ? 'Connected' : 'Disconnected'}</span>
          <span className="sm:hidden">{connected ? '●' : '○'}</span>
        </Badge>
        {agentsTotal !== undefined && agentsTotal > 0 && agentsOnline !== undefined && (
          /* #54 — surface agent liveness count alongside the server
             connection badge. Clamped in case of brief
             online>total reconnection races. */
          <span
            className="text-caption rounded-[var(--radius-sm)] border border-[var(--color-border)] px-1.5 py-0.5 text-[var(--color-foreground-muted)]"
            title={`${agentsOnline} of ${agentsTotal} agents online`}
            data-testid="room-header-agent-liveness"
          >
            agents {Math.min(agentsOnline, agentsTotal)}/{agentsTotal}
          </span>
        )}
        <RoomSettingsMenu
          onCreateSubRoom={onCreateSubRoom}
          onEditRoom={onEditRoom}
          onManageInvites={onManageInvites}
          onManageAgents={onManageAgents}
          onStopAllAgents={onStopAllAgents}
          onDeleteRoom={onDeleteRoom}
        />
      </div>
    </div>
  )
}

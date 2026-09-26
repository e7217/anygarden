import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Select } from '@/components/ui/select'
import { Hash, Users, Menu, ChevronLeft, EyeOff, Eye, Search, PanelRight, ListTree } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import RoomSettingsMenu from '@/components/RoomSettingsMenu'
import { EntityAvatar, type AvatarKind } from '@/components/EntityAvatar'
import { useLocale } from '@/i18n/LocaleProvider'

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
  /** Optional engine id (claude-code, codex-cli, gemini-cli, …).
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
  onManageWorkspaces?: () => void
  onCreateSubRoom?: () => void
  onEditRoom?: () => void
  onManageInvites?: () => void
  onStopAllAgents?: () => void
  onDeleteRoom?: () => void
  onOpenSidebar?: () => void
  onToggleParticipants?: () => void
  /** #237 — ephemeral mode state + toggle. ``undefined`` hides the
   *  control (non-DM rooms, or the caller didn't wire it yet).
   *  DM owners and admins may flip the flag; other members should
   *  receive ``undefined`` for ``onToggleEphemeral`` so the icon
   *  renders read-only. */
  ephemeral?: boolean
  onToggleEphemeral?: (next: boolean) => void
  /** #329 Phase 3 — search trigger. Was a separate row above the
   *  chat area; absorbed here so the chat surface stops paying for
   *  a row that ⌘K already covers. ``undefined`` hides the button
   *  (guest pages, routes without the search dialog). */
  onSearch?: () => void
  /** Thread layout A/B switch. Both handler and value must be
   *  supplied for the control to render; it is temporary scaffolding
   *  while the shape is being chosen, not a settled setting. */
  threadDisplayMode?: 'panel' | 'inline'
  onToggleThreadDisplayMode?: () => void
  /** #329 Phase 3 — artifacts trigger. Forwarded to the overflow
   *  menu so every room member (not just admins) can browse the
   *  agent-produced artifacts without the header growing another
   *  inline icon. */
  onShowArtifacts?: () => void
  /** #429 — admin-only room activity / multi-agent flow viewer. */
  onShowRoomActivity?: () => void
  /** #302 — slot for the right context rail toggle button. ChatPage
   *  passes <RightRailToggle/>; the header just gives it a place to
   *  live next to the settings menu. ``undefined`` hides it (e.g.
   *  guest pages or routes that don't host the rail). */
  rightRailSlot?: import('react').ReactNode
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
  onManageWorkspaces,
  onCreateSubRoom,
  onEditRoom,
  onManageInvites,
  onStopAllAgents,
  onDeleteRoom,
  onOpenSidebar,
  onToggleParticipants,
  ephemeral,
  onToggleEphemeral,
  onSearch,
  threadDisplayMode,
  onToggleThreadDisplayMode,
  onShowArtifacts,
  onShowRoomActivity,
  rightRailSlot,
}: RoomHeaderProps) {
  const navigate = useNavigate()
  const { t } = useLocale()
  const hasParent = parentBreadcrumb && parentBreadcrumb.length > 0
  const immediateParent = hasParent
    ? parentBreadcrumb![parentBreadcrumb!.length - 1]
    : null

  return (
    // Container query, not a viewport breakpoint: opening the thread
    // panel narrows this column while the viewport is unchanged, so
    // ``sm:``/``lg:`` cannot see the squeeze that collapsed the title.
    <div className="@container/header shrink-0 border-b border-[var(--color-border)] bg-[var(--color-surface)]">
      {/* Use the chat column's width, which also changes when the context
          rail or thread panel opens. On narrow columns, controls keep the
          first row and status moves below the room title. */}
      <div className="grid min-h-14 grid-cols-[minmax(0,1fr)_auto] items-center gap-x-1 gap-y-1 px-2 py-1.5 md:px-4 @[54rem]/header:grid-cols-[minmax(0,1fr)_auto_auto]">
      <div className="col-start-1 row-start-1 flex min-w-0 items-center gap-2">
        {onOpenSidebar && (
          <Button
            variant="ghost"
            size="icon"
            onClick={onOpenSidebar}
            className="shrink-0 md:hidden"
            aria-label={t('chat.openSidebar')}
          >
            <Menu className="h-5 w-5" />
          </Button>
        )}
        {immediateParent && (
          <button
            onClick={() => navigate(`/rooms/${immediateParent.id}`)}
            className="flex h-[var(--control-icon-size)] shrink-0 items-center gap-1 rounded-[var(--radius-sm)] px-1.5 text-xs text-[var(--color-foreground-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-foreground)] transition-colors"
            title={t('chat.backToParent', { name: immediateParent.name })}
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
        <h2 className="min-w-0 truncate text-base font-semibold text-[var(--color-foreground)]" title={roomName}>{roomName}</h2>
      </div>
      <div className="col-span-2 row-start-2 flex min-w-0 items-center gap-2 overflow-hidden pl-1 @[54rem]/header:col-span-1 @[54rem]/header:col-start-2 @[54rem]/header:row-start-1 @[54rem]/header:pl-0">
        {participantCount !== undefined && (
          onToggleParticipants ? (
            <button
              type="button"
              onClick={onToggleParticipants}
              // ``hover:bg-[var(--color-surface-hover)] cursor-pointer`` matches the
              // project-wide ghost-button convention recorded in
              // docs/history/STATUS.md (PR #31/#32).
              className="text-caption text-[var(--color-foreground-muted)] flex h-[var(--control-icon-size)] min-w-[var(--control-icon-size)] shrink-0 items-center justify-center gap-1 rounded-[var(--radius-sm)] px-1.5 hover:bg-[var(--color-surface-hover)] cursor-pointer"
              aria-label={t('chat.showParticipants', { count: participantCount })}
              title={t('chat.showParticipantsTitle')}
              data-testid="room-header-participants-toggle"
            >
              <Users className="h-4 w-4" />
              <span>{participantCount}</span>
            </button>
          ) : (
            <div className="text-caption text-[var(--color-foreground-muted)] flex items-center gap-1 whitespace-nowrap">
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
          <Select
            value={representativeAgentId ?? ''}
            onChange={(e) => onSetRepresentative(e.target.value || null)}
            className="h-[var(--control-icon-size)] max-w-40 flex-1 border-[var(--color-border)] px-2 text-sm @[54rem]/header:w-36 @[54rem]/header:flex-none"
            title={t('chat.setRepresentative')}
            aria-label={t('chat.setRepresentative')}
          >
            <option value="">{t('chat.noRepresentative')}</option>
            {agentParticipants.map((ap) => (
              <option key={ap.agent_id} value={ap.agent_id}>
                {ap.display_name}
                {ap.online === false ? t('chat.offlineSuffix') : ''}
              </option>
            ))}
          </Select>
        )}
        {isDm && onToggleEphemeral !== undefined && (
          /* #237 — active uses the design system's teal action token;
             inactive uses the shared surface and border tokens. */
          <button
            type="button"
            onClick={() => onToggleEphemeral(!ephemeral)}
            title={
              ephemeral
                ? t('chat.disableTemporary')
                : t('chat.enableTemporary')
            }
            aria-pressed={!!ephemeral}
            data-testid="room-header-ephemeral-toggle"
            className={`inline-flex size-[var(--control-icon-size)] shrink-0 items-center justify-center rounded-[var(--radius-sm)] transition-colors ${
              ephemeral
                ? 'bg-[var(--color-brand)] text-white hover:bg-[var(--color-brand-hover)]'
                : 'border border-[var(--color-border)] text-[var(--color-foreground-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-foreground)]'
            }`}
          >
            {ephemeral ? (
              <EyeOff className="h-4 w-4" />
            ) : (
              <Eye className="h-4 w-4" />
            )}
          </button>
        )}
        <Badge variant={connected ? 'success' : 'destructive'} aria-label={connected ? t('chat.connected') : t('chat.disconnected')}>
          <span className="hidden sm:inline">{connected ? t('chat.connected') : t('chat.disconnected')}</span>
          <span className="sm:hidden">{connected ? '●' : '○'}</span>
        </Badge>
        {agentsTotal !== undefined && agentsTotal > 0 && agentsOnline !== undefined && (
          /* #54 — surface agent liveness count alongside the server
             connection badge. Clamped in case of brief
             online>total reconnection races. */
          <span
            className="text-caption shrink-0 whitespace-nowrap rounded-[var(--radius-sm)] border border-[var(--color-border)] px-1.5 py-0.5 text-[var(--color-foreground-muted)]"
            title={t('chat.agentsOnlineTitle', { online: agentsOnline, total: agentsTotal })}
            data-testid="room-header-agent-liveness"
          >
            {t('chat.agentsOnline', { online: Math.min(agentsOnline, agentsTotal), total: agentsTotal })}
          </span>
        )}
      </div>
      <div className="col-start-2 row-start-1 flex shrink-0 items-center gap-0.5 @[54rem]/header:col-start-3">
        {onSearch && (
          /* #329 Phase 4 — direct search icon hidden below sm so the
             header strip stays uncluttered on phones. The same
             ``onSearch`` is forwarded to RoomSettingsMenu below as a
             menu fallback for mobile users (who can't type ⌘K). */
          <button
            type="button"
            onClick={onSearch}
            title={t('chat.searchShortcut')}
            aria-label={t('chat.searchMessages')}
            data-testid="room-header-search"
            className="hidden @[30rem]/header:inline-flex size-[var(--control-icon-size)] items-center justify-center rounded-[var(--radius-sm)] text-[var(--color-foreground-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-foreground)] transition-colors"
          >
            <Search className="h-4 w-4" />
          </button>
        )}
        {threadDisplayMode && onToggleThreadDisplayMode && (
          <button
            type="button"
            onClick={onToggleThreadDisplayMode}
            title={t('chat.threadModeTitle', { mode: t(threadDisplayMode === 'panel' ? 'chat.threadModePanel' : 'chat.threadModeInline') })}
            aria-label={t('chat.threadModeAction', { mode: t(threadDisplayMode === 'panel' ? 'chat.threadModePanel' : 'chat.threadModeInline'), next: t(threadDisplayMode === 'panel' ? 'chat.threadModeInline' : 'chat.threadModePanel') })}
            data-testid="thread-mode-toggle"
            className="hidden @[34rem]/header:inline-flex h-[var(--control-icon-size)] items-center gap-1 rounded-[var(--radius-sm)] px-2 text-badge text-[var(--color-foreground-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-foreground)] transition-colors"
          >
            {threadDisplayMode === 'panel' ? (
              <PanelRight className="h-4 w-4" />
            ) : (
              <ListTree className="h-4 w-4" />
            )}
            {t(threadDisplayMode === 'panel' ? 'chat.threadModePanel' : 'chat.threadModeInline')}
          </button>
        )}
        <RoomSettingsMenu
          onCreateSubRoom={onCreateSubRoom}
          onEditRoom={onEditRoom}
          onManageInvites={onManageInvites}
          onManageAgents={onManageAgents}
          onManageWorkspaces={onManageWorkspaces}
          onSearch={onSearch}
          threadDisplayMode={threadDisplayMode}
          onToggleThreadDisplayMode={onToggleThreadDisplayMode}
          onShowArtifacts={onShowArtifacts}
          onShowRoomActivity={onShowRoomActivity}
          onStopAllAgents={onStopAllAgents}
          onDeleteRoom={onDeleteRoom}
        />
        {rightRailSlot}
      </div>
      </div>
    </div>
  )
}

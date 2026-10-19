import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Hash, Users, Menu, ChevronLeft, EyeOff, Eye, Search } from 'lucide-react'
import { useEffect, useState } from 'react'
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

/** #897 — how long the socket may be down before the header says so.
 *  Every room switch starts disconnected and the first reconnect waits
 *  1s, so a shorter window would flash the badge on ordinary use. */
const DISCONNECT_GRACE_MS = 2000

interface RoomHeaderProps {
  roomName: string
  connected: boolean
  participantCount?: number
  /** #54/#897 — agent liveness. Only offline agents surface, as a
   *  mark on the participants button. ``agentsOnline`` can exceed
   *  ``agentsTotal`` briefly during reconnects; we clamp. */
  agentsOnline?: number
  agentsTotal?: number
  parentBreadcrumb?: ParentBreadcrumb[]
  /** True when the current room is a 1:1 DM with an agent. Drives
   *  the left-glyph swap from #-hash to an engine avatar. */
  isDm?: boolean
  /** The agent whose identity the DM carries. Only consulted when
   *  ``isDm`` is true. */
  dmAgent?: DmAgent
  onManageAgents?: () => void
  onManageWorkspaces?: () => void
  onCreateSubRoom?: () => void
  onEditRoom?: () => void
  onManageInvites?: () => void
  onStopAllAgents?: () => void
  /** ROOM-05 — forwarded to RoomSettingsMenu; undefined hides it. */
  onLeaveRoom?: () => void
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
 * Layout is one row with two zones (#897):
 * - Left: breadcrumb + room name. Always visible.
 * - Right: participant count (toggles the list popover, which also
 *   shows and sets the representative agent), the DM ephemeral toggle,
 *   search, a single ``…`` overflow menu for the admin mutation
 *   actions, and the context-rail toggle. Status is shown only when it
 *   needs attention: a warning mark on the participant count while an
 *   agent is offline, and a badge once the socket stays disconnected.
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
  isDm,
  dmAgent,
  onManageAgents,
  onManageWorkspaces,
  onCreateSubRoom,
  onEditRoom,
  onManageInvites,
  onStopAllAgents,
  onLeaveRoom,
  onDeleteRoom,
  onOpenSidebar,
  onToggleParticipants,
  ephemeral,
  onToggleEphemeral,
  onSearch,
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

  const agentsOffline =
    agentsTotal !== undefined && agentsOnline !== undefined
      ? Math.max(0, agentsTotal - Math.min(agentsOnline, agentsTotal))
      : 0
  const agentsOfflineLabel = agentsOffline > 0
    ? t(agentsOffline === 1 ? 'chat.agentsOfflineOne' : 'chat.agentsOfflineMany', { count: agentsOffline })
    : null

  const [showDisconnected, setShowDisconnected] = useState(false)
  useEffect(() => {
    if (connected) {
      setShowDisconnected(false)
      return
    }
    const timer = setTimeout(() => setShowDisconnected(true), DISCONNECT_GRACE_MS)
    return () => clearTimeout(timer)
  }, [connected])
  const disconnectedBadge = !connected && showDisconnected

  return (
    // Container query, not a viewport breakpoint: opening the thread
    // panel narrows this column while the viewport is unchanged, so
    // ``sm:``/``lg:`` cannot see the squeeze that collapsed the title.
    <div data-testid="room-header" className="@container/header shrink-0 border-b border-[var(--color-border)] bg-[var(--color-surface)]">
      {/* #897 — one row: title on the left, everything else on the
          right. Status only appears when it needs attention, so it no
          longer needs a second row on narrow columns. */}
      <div className="grid min-h-14 grid-cols-[minmax(0,1fr)_auto] items-center gap-x-1 px-2 py-1.5 md:px-4">
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
        <h1 className="min-w-0 truncate text-base font-semibold tracking-[-0.015em] text-[var(--color-foreground)]" title={roomName}>{roomName}</h1>
      </div>
      <div className="col-start-2 row-start-1 flex shrink-0 items-center gap-1">
        {participantCount !== undefined && (
          onToggleParticipants ? (
            <button
              type="button"
              onClick={onToggleParticipants}
              // ``hover:bg-[var(--color-surface-hover)] cursor-pointer`` matches the
              // project-wide ghost-button convention recorded in
              // docs/history/STATUS.md (PR #31/#32).
              className="relative text-caption text-[var(--color-foreground-muted)] flex h-[var(--control-icon-size)] min-w-[var(--control-icon-size)] shrink-0 items-center justify-center gap-1 rounded-[var(--radius-sm)] px-1.5 hover:bg-[var(--color-surface-hover)] cursor-pointer"
              aria-label={
                agentsOfflineLabel
                  ? `${t('chat.showParticipants', { count: participantCount })}, ${agentsOfflineLabel}`
                  : t('chat.showParticipants', { count: participantCount })
              }
              title={agentsOfflineLabel ?? t('chat.showParticipantsTitle')}
              data-testid="room-header-participants-toggle"
            >
              <Users className="h-4 w-4" />
              <span>{participantCount}</span>
              {agentsOfflineLabel && (
                /* #897 — the shape (a ringed dot on the icon) and the
                   accessible name carry the signal, not the amber alone. */
                <span
                  aria-hidden="true"
                  data-testid="room-header-agents-offline"
                  className="absolute right-0.5 top-0.5 size-2 rounded-full bg-[var(--color-warning)] ring-2 ring-[var(--color-surface)]"
                />
              )}
            </button>
          ) : (
            <div className="text-caption text-[var(--color-foreground-muted)] flex items-center gap-1 whitespace-nowrap">
              <Users className="h-4 w-4" />
              <span>{participantCount}</span>
            </div>
          )
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
                ? 'bg-[var(--color-brand)] text-[var(--color-on-brand)] hover:bg-[var(--color-brand-hover)]'
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
        {disconnectedBadge && (
          <Badge variant="destructive" data-testid="room-header-connection" role="status">
            {t('chat.disconnected')}
          </Badge>
        )}
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
        <RoomSettingsMenu
          onCreateSubRoom={onCreateSubRoom}
          onEditRoom={onEditRoom}
          onManageInvites={onManageInvites}
          onManageAgents={onManageAgents}
          onManageWorkspaces={onManageWorkspaces}
          onSearch={onSearch}
          onShowArtifacts={onShowArtifacts}
          onShowRoomActivity={onShowRoomActivity}
          onStopAllAgents={onStopAllAgents}
          onLeaveRoom={onLeaveRoom}
          onDeleteRoom={onDeleteRoom}
        />
        {rightRailSlot}
      </div>
      </div>
    </div>
  )
}

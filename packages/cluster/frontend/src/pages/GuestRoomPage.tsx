import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import ChatArea from '@/components/ChatArea'
import MessageInput from '@/components/MessageInput'
import ParticipantListPopover from '@/components/ParticipantListPopover'
import { useRoomParticipants } from '@/hooks/useRoomParticipants'
import { clearAuthSession, getAuthToken, isGuestSession } from '@/lib/authStorage'
import { useWebSocket } from '@/hooks/useWebSocket'
import type { MentionOption } from '@/components/MentionPopover'
import { Hash, LogOut, Settings2, Users } from 'lucide-react'
import { useLocale } from '@/i18n/LocaleProvider'
import { LocaleToggle } from '@/i18n/LocaleToggle'
import { ThemeToggle } from '@/theme/ThemeToggle'

/**
 * ``/g/:roomId``
 *
 * Single-room shell for an anonymous guest. The guest JWT is stored
 * under the shared ``anygarden_token`` slot; we rely on
 * ``anygarden_is_guest`` + ``anygarden_guest_room_id`` sentinels to
 * distinguish the flow from a registered-user session so (a) we
 * don't call ``/auth/me`` (403 for guests) and (b) we refuse to
 * render if the JWT was issued for a different room.
 *
 * Everything outside this room is intentionally unreachable —
 * sidebar, projects, admin surfaces, etc. If the guest hits the
 * logout control the JWT is dropped and they're sent back to the
 * login screen. §11.9 of the design doc.
 */
export default function GuestRoomPage() {
  const { t } = useLocale()
  const { roomId } = useParams<{ roomId: string }>()
  const navigate = useNavigate()
  const [participantsOpen, setParticipantsOpen] = useState(false)

  const isGuest = isGuestSession()
  const boundRoomId = localStorage.getItem('anygarden_guest_room_id')
  const displayName = localStorage.getItem('anygarden_guest_display_name') ?? ''
  const scope = isGuest && getAuthToken() && (!boundRoomId || boundRoomId === roomId) ? roomId ?? null : null
  const { participants, name: roomName, errorStatus, loaded, loading, refresh } = useRoomParticipants(scope)
  const myParticipantId = Object.values(participants).find(p => p.is_anonymous && p.display_name === displayName)?.id ?? null
  const initError = errorStatus ? t('guest.loadFailed', { status: errorStatus }) : null

  // Defend against stale URLs — a guest with a valid JWT but typing a
  // different room UUID into the address bar must be bounced.
  useEffect(() => {
    if (!getAuthToken() || !isGuest) {
      navigate('/login', { replace: true })
      return
    }
    if (roomId && boundRoomId && roomId !== boundRoomId) {
      navigate(`/g/${boundRoomId}`, { replace: true })
    }
  }, [roomId, boundRoomId, isGuest, navigate])

  useEffect(() => {
    if (errorStatus === 401 || errorStatus === 403) {
      clearAuthSession()
      navigate('/login', { replace: true })
    }
  }, [errorStatus, navigate])

  const { messages, connected, typingUsers, typingStages, send, sendTyping } = useWebSocket(
    scope,
  )

  // Surface every participant in the ``@``-autocomplete: the server
  // already restricts a guest's room scope, and any mention outside
  // the current room is rejected by the ``@user``-token resolution
  // rules (§11.6). Mirrors the shape used by ``ChatPage`` so the
  // renderer/agent-routing gates see the same ``participant.id``
  // token namespace downstream.
  const mentionParticipants: MentionOption[] = useMemo(
    () =>
      Object.values(participants).map((p) => ({
        id: p.id,
        display: p.display_name,
        kind: (p.kind === 'agent' ? 'agent' : 'user') as 'user' | 'agent',
        // #271 — surface the agent description so guests in a single
        // room can also distinguish multiple agents in the popover.
        description: p.description ?? null,
      })),
    [participants],
  )

  const handleLogout = useCallback(() => {
    clearAuthSession()
    navigate('/login', { replace: true })
  }, [navigate])

  if (initError && !loaded) {
    return (
      <div className="flex min-h-screen items-center justify-center p-4">
        <div className="max-w-sm space-y-4 text-center">
          <div className="text-lg font-semibold">{t('guest.unavailable')}</div>
          <div className="text-sm text-[var(--color-foreground-muted)]">
            {initError}
          </div>
          <Button onClick={() => void refresh()} disabled={loading} variant="outline">
            {t('common.retry')}
          </Button>
          <Button onClick={handleLogout} variant="ghost">
            {t('guest.leave')}
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-dvh flex-col bg-[var(--color-background)]">
      {initError && (
        <div role="alert" className="flex items-center justify-between gap-3 border-b border-[var(--color-border)] px-3 py-2 text-sm">
          <span>{initError}</span>
          <Button variant="ghost" size="sm" onClick={() => void refresh()} disabled={loading}>{t('common.retry')}</Button>
        </div>
      )}
      {/* Minimal top bar. No sidebar toggle, no admin widgets. */}
      <div className="relative">
        <div className="flex h-14 items-center justify-between gap-2 border-b border-[var(--color-border)] bg-[var(--color-surface)] px-3 md:px-6">
          <div className="flex min-w-0 items-center gap-2">
            <Hash className="h-5 w-5 shrink-0 text-[var(--color-foreground-muted)]" />
            <div className="truncate text-sm font-semibold" title={roomName}>{roomName || t('guest.room')}</div>
            <Badge variant="outline" className="ml-2 hidden sm:inline-flex">
              {t('guest.guest', { name: displayName })}
            </Badge>
          </div>
          <div className="flex shrink-0 items-center gap-0.5 sm:gap-2">
            {/* Participant count + popover toggle. §11.9 doesn't
                spell this out explicitly but hiding the roster from
                guests felt strictly worse than letting them see who
                they're talking to — the server returns the same
                room detail either way. */}
            <button
              type="button"
              onClick={() => setParticipantsOpen((v) => !v)}
              // Ghost-button hover convention (see
              // docs/history/STATUS.md — ``hover:bg-black/5
              // cursor-pointer`` applied globally to ghost buttons).
              className="text-caption flex min-h-11 min-w-11 items-center justify-center gap-1 rounded-[var(--radius-sm)] text-[var(--color-foreground-muted)] hover:bg-[var(--color-surface-alt)] cursor-pointer"
              title={t('guest.participants')}
              aria-label={t('guest.participants')}
              data-testid="guest-header-participants-toggle"
            >
              <Users className="h-4 w-4" />
              <span>{Object.keys(participants).length}</span>
            </button>
            <Badge variant={connected ? 'default' : 'destructive'}>
              <span className="hidden sm:inline">
                {connected ? t('guest.connected') : t('guest.disconnected')}
              </span>
              <span className="sm:hidden" aria-label={connected ? t('guest.connected') : t('guest.disconnected')}>{connected ? '●' : '○'}</span>
            </Badge>
            <Dialog>
              <DialogTrigger asChild>
                <Button variant="ghost" size="icon" aria-label={t('guest.preferences')} title={t('guest.preferences')}>
                  <Settings2 className="h-4 w-4" />
                </Button>
              </DialogTrigger>
              <DialogContent className="max-w-sm">
                <DialogHeader><DialogTitle>{t('guest.preferences')}</DialogTitle></DialogHeader>
                <div className="flex items-center justify-between gap-3">
                  <span className="text-sm text-[var(--color-foreground-muted)]">{t('common.language')}</span>
                  <LocaleToggle compact />
                </div>
                <div className="flex items-center justify-between gap-3">
                  <span className="text-sm text-[var(--color-foreground-muted)]">{t('common.theme')}</span>
                  <ThemeToggle showLabel />
                </div>
              </DialogContent>
            </Dialog>
            <Button variant="ghost" size="icon" onClick={handleLogout} title={t('guest.leaveRoom')} aria-label={t('guest.leaveRoom')}>
              <LogOut className="h-4 w-4" />
            </Button>
          </div>
        </div>
        <ParticipantListPopover
          participants={participants}
          open={participantsOpen}
          onClose={() => setParticipantsOpen(false)}
          myParticipantId={myParticipantId}
        />
      </div>

      <div className="flex min-w-0 flex-1 flex-col">
        <ChatArea
          messages={messages}
          participants={participants}
          myParticipantId={myParticipantId}
          typingUsers={typingUsers}
          typingStages={typingStages}
        />
        <MessageInput
          onSend={send}
          onTyping={sendTyping}
          disabled={!connected}
          mentionUsers={mentionParticipants}
          // Empty ``mentionRooms`` disables the ``#`` autocomplete —
          // guests can't route cross-room anyway (server strips the
          // mention, §11.6). Leaving the popover in would just be
          // confusing UI.
          mentionRooms={[]}
        />
      </div>
    </div>
  )
}

import { useEffect, useMemo, useRef } from 'react'
import { X } from 'lucide-react'
import type { Participant } from '@/pages/ChatPage'
import PresenceDot from '@/components/PresenceDot'
import { EntityAvatar, type AvatarKind, type EntityKind } from '@/components/EntityAvatar'
import type { PresenceMap } from '@/hooks/useParticipantPresence'
import { useLocale } from '@/i18n/LocaleProvider'
import { useFeedback } from '@/components/feedback/FeedbackProvider'

interface Props {
  participants: Record<string, Participant>
  /**
   * Realtime presence map from ``useParticipantPresence``. When
   * present, each row renders a ``<PresenceDot>`` that updates
   * without re-fetching the room. Optional so the popover still
   * works for callers that haven't wired presence yet (e.g. the
   * guest room view).
   */
  presence?: PresenceMap
  open: boolean
  onClose: () => void
  myParticipantId?: string | null
  anchorRight?: boolean
  /** When provided, renders a remove (✕) button for removable rows.
   *  The server remains the sole authority on who may be removed —
   *  the parent should only pass this when the caller is a global
   *  admin or a room admin/owner. Rows for the caller themselves and
   *  for ``owner``-role participants are never shown a button (the
   *  first to avoid accidental self-ejection via this endpoint; the
   *  second because owner removal will arrive in a later PR with its
   *  own confirmation flow).
   */
  onRemove?: (participantId: string) => Promise<void> | void
}

/**
 * Dropdown list of room members.
 *
 * Rendered near the participant-count button in ``RoomHeader`` and
 * the equivalent badge on ``GuestRoomPage``. Kept deliberately
 * simple — no scroll virtualization, no lazy load — rooms run on
 * the order of dozens of participants and a flat list is faster to
 * scan than a tree. The popover closes on outside click / Escape.
 */
export default function ParticipantListPopover({
  participants,
  presence,
  open,
  onClose,
  myParticipantId,
  anchorRight = true,
  onRemove,
}: Props) {
  const { t } = useLocale()
  const { confirm } = useFeedback()
  const rootRef = useRef<HTMLDivElement>(null)

  // Stable ordering: agents first (they drive the room), then
  // registered users alpha, then guests alpha. Keeps the layout
  // from shuffling when a new guest joins.
  const sorted = useMemo(() => {
    const list = Object.values(participants)
    const groupRank = (p: Participant): number => {
      if (p.kind === 'agent') return 0
      if (p.is_anonymous) return 2
      return 1
    }
    return [...list].sort((a, b) => {
      const ra = groupRank(a)
      const rb = groupRank(b)
      if (ra !== rb) return ra - rb
      return a.display_name.localeCompare(b.display_name)
    })
  }, [participants])

  useEffect(() => {
    if (!open) return
    const onDocClick = (e: MouseEvent) => {
      if (!rootRef.current) return
      if (!rootRef.current.contains(e.target as Node)) onClose()
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('mousedown', onDocClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDocClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [open, onClose])

  if (!open) return null

  return (
    <div
      ref={rootRef}
      className={
        'absolute top-12 z-40 w-64 rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-surface-elevated)] shadow-lg ' +
        (anchorRight ? 'right-4' : 'left-4')
      }
      // ``role="dialog"`` would contract us into the APG-specified
      // focus-trap territory. The popover is a simple one-shot list
      // with no interactive descendants, so ``role="group"`` is the
      // honest semantic — screen readers announce it as a labelled
      // group and keyboard users can Tab through without getting
      // trapped.
      role="group"
      aria-label={t('participants.title')}
    >
      <div className="border-b border-[var(--color-border)] px-3 py-2 text-xs font-medium text-[var(--color-foreground-muted)]">
        {t(sorted.length === 1 ? 'participants.countOne' : 'participants.countMany', { count: sorted.length })}
      </div>
      <ul className="max-h-80 overflow-y-auto py-1">
        {sorted.map((p) => {
          const isMe = p.id === myParticipantId
          // Show the remove button only when all of these hold:
          //  - caller has the capability (onRemove provided)
          //  - row is not the caller themselves (self-removal is 400
          //    on the server; the correct path is a future leave flow)
          //  - row is not a room owner (owner removal is out of scope
          //    for this PR; matches the server policy of keeping at
          //    least one admin/owner alive)
          const canRemoveThis =
            !!onRemove && !isMe && p.role !== 'owner'
          const avatarKind: EntityKind =
            p.kind === 'agent'
              ? 'agent'
              : p.is_anonymous
                ? 'guest'
                : 'user'
          return (
            <li
              key={p.id}
              className="flex items-center gap-2 px-3 py-2 text-sm"
            >
              <EntityAvatar
                id={p.id}
                name={p.display_name || p.id}
                kind={avatarKind}
                size="xs"
                avatarKind={
                  avatarKind === 'agent'
                    ? ((p.avatar_kind as AvatarKind | null | undefined) ?? null)
                    : null
                }
                avatarValue={avatarKind === 'agent' ? (p.avatar_value ?? null) : null}
              />
              <PresenceDot
                online={
                  presence?.[p.id]?.online ?? Boolean(p.online)
                }
                lastSeenAt={
                  presence?.[p.id]?.lastSeenAt ?? p.last_seen_at ?? null
                }
              />
              {/* Stack name + description in a column so the
                  description (when present) reads as a secondary line
                  underneath the name. The column shrinks to fit the
                  remaining row width so badges on the right keep
                  their position. */}
              <span className="flex min-w-0 flex-1 flex-col">
                <span className="truncate">
                  {p.display_name || p.id.slice(0, 8)}
                  {isMe && (
                    <span className="ml-1 text-[var(--color-foreground-muted)]">
                      {t('participants.you')}
                    </span>
                  )}
                </span>
                {/* #271 — agent description as secondary text. Hidden
                    when absent so user/guest rows and pre-#271 agents
                    keep their original single-line layout. */}
                {p.description?.trim() ? (
                  <span
                    className="truncate text-[11px] text-[var(--color-foreground-subtle)]"
                    data-testid={`participant-description-${p.id}`}
                  >
                    {p.description}
                  </span>
                ) : null}
              </span>
              <span className="ml-auto flex shrink-0 items-center gap-1">
                {p.kind === 'agent' && (
                  <span className="rounded-[var(--radius-sm)] border border-[var(--color-border)] px-1.5 py-0 text-[10px] uppercase tracking-wide text-[var(--color-foreground-muted)]">
                    {t('participants.agent')}
                  </span>
                )}
                {p.is_anonymous && (
                  <span className="rounded-[var(--radius-sm)] border border-[var(--color-brand)] px-1.5 py-0 text-[10px] uppercase tracking-wide text-[var(--color-brand-text)]">
                    {t('participants.guest')}
                  </span>
                )}
                {/* Show the role badge only for registered users —
                    guests don't have room roles in any meaningful
                    sense and showing both badges at once is visually
                    noisy. A genuine owner+guest row can't exist
                    today, but the guard keeps it that way if the
                    data ever slips. */}
                {!p.is_anonymous && (p.role === 'owner' || p.role === 'admin') && (
                  <span className="rounded-[var(--radius-sm)] bg-[var(--color-brand-tint-bg)] px-1.5 py-0 text-[10px] uppercase tracking-wide text-[var(--color-brand-tint-text)]">
                    {t(p.role === 'owner' ? 'participants.owner' : 'participants.admin')}
                  </span>
                )}
                {canRemoveThis && (
                  <button
                    type="button"
                    aria-label={t('participants.remove', { name: p.display_name || t('participants.fallback') })}
                    className="ml-1 flex min-h-11 min-w-11 items-center justify-center rounded-[var(--radius-sm)] text-[var(--color-destructive)] hover:bg-[var(--color-danger-soft)]"
                    onClick={async () => {
                      const name = p.display_name || t('participants.fallback')
                      if (!await confirm({
                        title: t('participants.removeTitle'),
                        description: t('participants.removeConfirm', { name }),
                        confirmLabel: t('participants.removeTitle'),
                        destructive: true,
                      })) {
                        return
                      }
                      void onRemove!(p.id)
                    }}
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                )}
              </span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

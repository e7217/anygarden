import { useEffect, useMemo, useRef } from 'react'
import { X } from 'lucide-react'
import type { Participant } from '@/pages/ChatPage'
import PresenceDot from '@/components/PresenceDot'
import { EntityAvatar, type EntityKind } from '@/components/EntityAvatar'
import type { PresenceMap } from '@/hooks/useParticipantPresence'

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
        'absolute top-12 z-40 w-64 rounded-[var(--radius-md)] border border-[var(--color-border)] bg-white shadow-lg ' +
        (anchorRight ? 'right-4' : 'left-4')
      }
      // ``role="dialog"`` would contract us into the APG-specified
      // focus-trap territory. The popover is a simple one-shot list
      // with no interactive descendants, so ``role="group"`` is the
      // honest semantic — screen readers announce it as a labelled
      // group and keyboard users can Tab through without getting
      // trapped.
      role="group"
      aria-label="Room participants"
    >
      <div className="border-b border-[var(--color-border)] px-3 py-2 text-xs font-medium text-[var(--color-foreground-muted)]">
        {sorted.length} participant{sorted.length === 1 ? '' : 's'}
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
              />
              <PresenceDot
                online={
                  presence?.[p.id]?.online ?? Boolean(p.online)
                }
                lastSeenAt={
                  presence?.[p.id]?.lastSeenAt ?? p.last_seen_at ?? null
                }
              />
              <span className="truncate">
                {p.display_name || p.id.slice(0, 8)}
                {isMe && (
                  <span className="ml-1 text-[var(--color-foreground-muted)]">
                    (you)
                  </span>
                )}
              </span>
              <span className="ml-auto flex shrink-0 items-center gap-1">
                {p.kind === 'agent' && (
                  <span className="rounded-[var(--radius-sm)] border border-[var(--color-border)] px-1.5 py-0 text-[10px] uppercase tracking-wide text-[var(--color-foreground-muted)]">
                    agent
                  </span>
                )}
                {p.is_anonymous && (
                  <span className="rounded-[var(--radius-sm)] border border-[var(--color-brand)] px-1.5 py-0 text-[10px] uppercase tracking-wide text-[var(--color-brand)]">
                    guest
                  </span>
                )}
                {/* Show the role badge only for registered users —
                    guests don't have room roles in any meaningful
                    sense and showing both badges at once is visually
                    noisy. A genuine owner+guest row can't exist
                    today, but the guard keeps it that way if the
                    data ever slips. */}
                {!p.is_anonymous && (p.role === 'owner' || p.role === 'admin') && (
                  <span className="rounded-[var(--radius-sm)] bg-[color:color-mix(in_srgb,var(--color-brand)_15%,transparent)] px-1.5 py-0 text-[10px] uppercase tracking-wide text-[var(--color-brand)]">
                    {p.role}
                  </span>
                )}
                {canRemoveThis && (
                  <button
                    type="button"
                    aria-label={`Remove ${p.display_name || 'participant'} from this room`}
                    className="ml-1 rounded-[var(--radius-sm)] p-1 text-red-600 hover:bg-red-50"
                    onClick={() => {
                      // A native confirm keeps the component low-
                      // dependency; this is a rare destructive
                      // operation and the prompt is enough of a speed
                      // bump to prevent slips.
                      const name = p.display_name || 'this participant'
                      if (!window.confirm(`Remove ${name} from this room?`)) {
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

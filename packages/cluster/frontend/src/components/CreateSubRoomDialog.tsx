/**
 * CreateSubRoomDialog — spawn a child room under an existing room.
 *
 * The server-side rule in
 * ``anygarden-server/anygarden/rooms/service.py::create_sub_room`` is:
 *
 *   1. The ``creator_participant_id`` must be a member of the
 *      parent room.
 *   2. Every id in ``participants`` must ALSO be a member of the
 *      parent room.
 *   3. Self-reference is blocked server-side.
 *
 * So this dialog loads the parent's participant list, pre-selects
 * the current user's participant id as ``creator_participant_id``,
 * and offers the remaining parent members as optional invitees via
 * checkbox.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { apiFetch } from '@/lib/api'
import { useLocale } from '@/i18n/LocaleProvider'

interface ParentParticipant {
  id: string
  display_name: string
  kind: string
  user_id?: string
  agent_id?: string
}

interface ParentRoom {
  id: string
  name: string
  participants: ParentParticipant[]
}

interface Props {
  parentRoomId: string
  parentRoomName: string
  /** ``Participant.id`` of the current user in the parent room —
   *  required by the server as ``creator_participant_id``. If the
   *  dialog is opened without a valid value (e.g. the user is not
   *  yet a member of the parent), the create button stays
   *  disabled and an explanatory error is shown. */
  myParticipantId: string | null
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Called with the new room object on successful create. The
   *  parent is responsible for navigating / refetching. */
  onCreated: (newRoom: { id: string; name: string }) => void
}

export default function CreateSubRoomDialog({
  parentRoomId,
  parentRoomName,
  myParticipantId,
  open,
  onOpenChange,
  onCreated,
}: Props) {
  const { t } = useLocale()
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [parent, setParent] = useState<ParentRoom | null>(null)
  const [selectedInvitees, setSelectedInvitees] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(false)
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Fetch parent participants every time the dialog opens. We
  // don't cache across opens because another admin may have
  // changed parent membership in the meantime and the client
  // still has stale state.
  const reload = useCallback(async () => {
    if (!parentRoomId) return
    setLoading(true)
    setError(null)
    try {
      const resp = await apiFetch(`/api/v1/rooms/${parentRoomId}`)
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}))
        throw new Error(body.detail || '__subroom_load_failed__')
      }
      const data = await resp.json()
      setParent({
        id: data.id,
        name: data.name,
        participants: data.participants ?? [],
      })
      // Reset the invitee pick every open so a previous session's
      // selection doesn't carry over.
      setSelectedInvitees(new Set())
      setName('')
      setDescription('')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
    setLoading(false)
  }, [parentRoomId])

  useEffect(() => {
    if (open && parentRoomId) void reload()
  }, [open, parentRoomId, reload])

  const toggleInvitee = (participantId: string) => {
    setSelectedInvitees(prev => {
      const next = new Set(prev)
      if (next.has(participantId)) next.delete(participantId)
      else next.add(participantId)
      return next
    })
  }

  // Filter the parent's participant list into "selectable" — i.e.
  // everyone except the current user (who is auto-added as the
  // creator, so exposing them as a checkbox would be confusing).
  const invitees = useMemo(() => {
    if (!parent) return []
    return parent.participants.filter(p => p.id !== myParticipantId)
  }, [parent, myParticipantId])

  const canSubmit =
    !creating &&
    !loading &&
    name.trim().length > 0 &&
    myParticipantId !== null

  const handleCreate = async () => {
    if (!canSubmit || !myParticipantId) return
    setCreating(true)
    setError(null)
    try {
      const resp = await apiFetch(
        `/api/v1/rooms/${parentRoomId}/sub-rooms`,
        {
          method: 'POST',
          body: JSON.stringify({
            name: name.trim(),
            description: description.trim() || null,
            participants: Array.from(selectedInvitees),
            is_dm: false,
            creator_participant_id: myParticipantId,
          }),
        },
      )
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}))
        throw new Error(body.detail || t('subroom.createFailed'))
      }
      const newRoom = await resp.json()
      onCreated({ id: newRoom.id, name: newRoom.name })
      onOpenChange(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
    setCreating(false)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md max-h-[min(90dvh,52rem)] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{t('subroom.create')}</DialogTitle>
          <DialogDescription>
            {t('subroom.description', { name: parentRoomName })}
          </DialogDescription>
        </DialogHeader>

        {loading ? (
          <div className="py-6 text-center text-caption text-[var(--color-foreground-muted)]">
            {t('subroom.loadingMembers')}
          </div>
        ) : (
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <Label htmlFor="sub-room-name">{t('rooms.name')}</Label>
              <Input
                id="sub-room-name"
                placeholder={t('subroom.namePlaceholder')}
                value={name}
                onChange={e => setName(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter' && canSubmit) {
                    e.preventDefault()
                    void handleCreate()
                  }
                }}
                autoFocus
                data-testid="sub-room-name-input"
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="sub-room-desc">{t('subroom.descriptionLabel')}</Label>
              <textarea
                id="sub-room-desc"
                className="flex w-full rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-foreground)] placeholder:text-[var(--color-foreground-muted)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-focus)] focus:ring-offset-1 resize-none"
                placeholder={t('subroom.descriptionPlaceholder')}
                rows={2}
                value={description}
                onChange={e => setDescription(e.target.value)}
                data-testid="sub-room-desc-input"
              />
            </div>

            {myParticipantId === null && (
              <div className="rounded-[var(--radius-md)] border border-[color:color-mix(in_srgb,var(--color-warning)_30%,transparent)] bg-[color:color-mix(in_srgb,var(--color-warning)_10%,transparent)] px-3 py-2 text-sm text-[var(--color-warning)]">
                {t('subroom.notMember')}
              </div>
            )}

            <div className="space-y-2">
              <Label>{t('subroom.invite')}</Label>
              {invitees.length === 0 ? (
                <p className="text-caption text-[var(--color-foreground-subtle)]">
                  {t('subroom.noInvitees')}
                </p>
              ) : (
                <div className="max-h-48 overflow-y-auto rounded-[var(--radius-md)] border border-[var(--color-border)]">
                  {invitees.map(p => (
                    <label
                      key={p.id}
                      className="flex min-h-11 items-center gap-2 px-3 py-1.5 text-sm hover:bg-[var(--color-surface-hover)] cursor-pointer"
                    >
                      <input
                        type="checkbox"
                        checked={selectedInvitees.has(p.id)}
                        onChange={() => toggleInvitee(p.id)}
                        data-testid={`sub-room-invitee-${p.id}`}
                      />
                      <span className="truncate">
                        {p.display_name}
                        <span className="ml-1 text-xs text-[var(--color-foreground-muted)]">
                          ({p.kind})
                        </span>
                      </span>
                    </label>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {error ? (
          <div className="rounded-[var(--radius-md)] border border-[color:color-mix(in_srgb,var(--color-warning)_30%,transparent)] bg-[color:color-mix(in_srgb,var(--color-warning)_10%,transparent)] px-3 py-2 text-sm text-[var(--color-warning)]">
            {error === '__subroom_load_failed__' ? t('subroom.loadFailed') : error}
          </div>
        ) : null}

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={creating}>
            {t('common.cancel')}
          </Button>
          <Button
            onClick={handleCreate}
            disabled={!canSubmit}
            data-testid="sub-room-create"
          >
            {creating ? t('rooms.creating') : t('common.create')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

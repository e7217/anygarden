/**
 * CreateSubRoomDialog — spawn a child room under an existing room.
 *
 * The server-side rule in
 * ``doorae-server/doorae/rooms/service.py::create_sub_room`` is:
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
        throw new Error(body.detail || 'Failed to load parent room')
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
        throw new Error(body.detail || 'Failed to create sub-room')
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
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Create sub-room</DialogTitle>
          <DialogDescription>
            A sub-room is a focused side channel under{' '}
            <span className="font-medium text-[var(--color-foreground)]">
              {parentRoomName}
            </span>
            . Only members of the parent can be invited.
          </DialogDescription>
        </DialogHeader>

        {loading ? (
          <div className="py-6 text-center text-caption text-[var(--color-foreground-muted)]">
            Loading parent members…
          </div>
        ) : (
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <Label htmlFor="sub-room-name">Name</Label>
              <Input
                id="sub-room-name"
                placeholder="design-spike"
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
              <Label htmlFor="sub-room-desc">Description (optional)</Label>
              <textarea
                id="sub-room-desc"
                className="flex w-full rounded-[var(--radius-md)] border border-[var(--color-border)] bg-white px-3 py-2 text-sm placeholder:text-[var(--color-foreground-muted)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand)] focus:ring-offset-1 resize-none"
                placeholder="이 서브룸의 목적을 설명하세요 (에이전트 delegation 판단에 사용됩니다)"
                rows={2}
                value={description}
                onChange={e => setDescription(e.target.value)}
                data-testid="sub-room-desc-input"
              />
            </div>

            {myParticipantId === null && (
              <div className="rounded-[var(--radius-md)] border border-[color:color-mix(in_srgb,var(--color-warning)_30%,transparent)] bg-[color:color-mix(in_srgb,var(--color-warning)_10%,transparent)] px-3 py-2 text-sm text-[var(--color-warning)]">
                You're not a member of the parent room — refresh the
                room and try again.
              </div>
            )}

            <div className="space-y-2">
              <Label>Invite parent members (optional)</Label>
              {invitees.length === 0 ? (
                <p className="text-caption text-[var(--color-foreground-subtle)]">
                  The parent has no other members to invite.
                </p>
              ) : (
                <div className="max-h-48 overflow-y-auto rounded-[var(--radius-md)] border border-[var(--color-border)]">
                  {invitees.map(p => (
                    <label
                      key={p.id}
                      className="flex items-center gap-2 px-3 py-1.5 text-sm hover:bg-[var(--color-surface-alt)] cursor-pointer"
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
            {error}
          </div>
        ) : null}

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={creating}>
            Cancel
          </Button>
          <Button
            onClick={handleCreate}
            disabled={!canSubmit}
            data-testid="sub-room-create"
          >
            {creating ? 'Creating…' : 'Create'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

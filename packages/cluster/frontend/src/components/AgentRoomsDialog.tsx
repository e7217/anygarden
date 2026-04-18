import { useState, useEffect, useCallback } from 'react'
import { apiFetch } from '@/lib/api'
import { Button } from '@/components/ui/button'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription,
} from '@/components/ui/dialog'
import { Plus, X } from 'lucide-react'
import { EntityAvatar } from '@/components/EntityAvatar'

interface RoomInfo { id: string; name: string; project_id: string }

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  agentId: string | null
  /** Optional callback fired after every mutation so the parent can
   * refresh its own state (e.g. a machine-detail agent list that
   * displays the comma-joined room names inline). */
  onChange?: () => void
}

/** Manage the rooms an agent is a participant of.
 *
 * Extracted from AdminAgents so the Machines page can reuse the same
 * assign/unassign UX without copy-pasting three API endpoints and the
 * split "assigned / available" rendering.
 */
export default function AgentRoomsDialog({ open, onOpenChange, agentId, onChange }: Props) {
  const [assignedRooms, setAssignedRooms] = useState<RoomInfo[]>([])
  const [availableRooms, setAvailableRooms] = useState<RoomInfo[]>([])
  const [loading, setLoading] = useState(false)

  const fetchRooms = useCallback(async (id: string) => {
    setLoading(true)
    try {
      const assignedResp = await apiFetch(`/api/v1/agents/${id}/rooms`)
      const rawAssigned = assignedResp.ok ? await assignedResp.json() : []
      // Drop DM rooms from the display. The agent is still a
      // participant on the backend — we just don't want an admin
      // to accidentally detach a DM from this dialog.
      const assigned: RoomInfo[] = rawAssigned
        .filter((r: { is_dm?: boolean }) => !r.is_dm)
        .map((r: { room_id: string; room_name: string }) => ({
          id: r.room_id,
          name: r.room_name,
          project_id: '',
        }))
      setAssignedRooms(assigned)

      // is_dm=false: DM rooms are auto-created 1:1 channels between
      // a user and an agent; they cannot be meaningfully assigned to
      // a different agent, so they must not appear in the
      // "Available Rooms" list at all.
      const projResp = await apiFetch('/api/v1/projects')
      const projects = projResp.ok ? await projResp.json() : []
      const allRooms: RoomInfo[] = []
      for (const proj of projects) {
        const roomResp = await apiFetch(`/api/v1/rooms?project_id=${proj.id}&is_dm=false`)
        if (roomResp.ok) {
          const rooms = await roomResp.json()
          allRooms.push(...rooms.map((r: RoomInfo) => ({
            id: r.id, name: r.name, project_id: r.project_id,
          })))
        }
      }
      const assignedIds = new Set(assigned.map(r => r.id))
      setAvailableRooms(allRooms.filter(r => !assignedIds.has(r.id)))
    } catch { /* ignore */ }
    setLoading(false)
  }, [])

  useEffect(() => {
    if (open && agentId) void fetchRooms(agentId)
  }, [open, agentId, fetchRooms])

  const addRoom = async (roomId: string) => {
    if (!agentId) return
    await apiFetch(`/api/v1/agents/${agentId}/rooms`, {
      method: 'POST',
      body: JSON.stringify({ room_id: roomId }),
    })
    await fetchRooms(agentId)
    onChange?.()
  }

  const removeRoom = async (roomId: string) => {
    if (!agentId) return
    await apiFetch(`/api/v1/agents/${agentId}/rooms/${roomId}`, { method: 'DELETE' })
    await fetchRooms(agentId)
    onChange?.()
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Manage Rooms</DialogTitle>
          <DialogDescription>Assign or remove this agent from rooms.</DialogDescription>
        </DialogHeader>
        {loading ? (
          <div className="py-8 text-center text-caption text-[var(--color-foreground-muted)]">
            Loading rooms...
          </div>
        ) : (
          <div className="space-y-5 py-2">
            <div>
              <h3 className="text-badge uppercase text-[var(--color-foreground-muted)] mb-2 tracking-wider">
                Assigned Rooms
              </h3>
              {assignedRooms.length === 0 ? (
                <p className="text-caption text-[var(--color-foreground-subtle)]">No rooms assigned</p>
              ) : (
                <div className="space-y-2">
                  {assignedRooms.map(room => (
                    <div key={room.id} className="flex items-center justify-between rounded-[var(--radius-md)] bg-[var(--color-surface-alt)] border border-[var(--color-border)] px-3 py-2">
                      <span className="flex items-center gap-2 min-w-0">
                        <EntityAvatar id={room.id} name={room.name} kind="room" size="sm" />
                        <span className="truncate text-sm font-medium text-[var(--color-foreground)]">{room.name}</span>
                      </span>
                      <Button variant="ghost" size="icon" onClick={() => removeRoom(room.id)} title="Remove room">
                        <X className="h-4 w-4 text-[var(--color-warning)]" />
                      </Button>
                    </div>
                  ))}
                </div>
              )}
            </div>
            <div>
              <h3 className="text-badge uppercase text-[var(--color-foreground-muted)] mb-2 tracking-wider">
                Available Rooms
              </h3>
              {availableRooms.length === 0 ? (
                <p className="text-caption text-[var(--color-foreground-subtle)]">No available rooms</p>
              ) : (
                <div className="space-y-2">
                  {availableRooms.map(room => (
                    <div key={room.id} className="flex items-center justify-between rounded-[var(--radius-md)] bg-[var(--color-surface-alt)] border border-[var(--color-border)] px-3 py-2">
                      <span className="flex items-center gap-2 min-w-0">
                        <EntityAvatar id={room.id} name={room.name} kind="room" size="sm" />
                        <span className="truncate text-sm font-medium text-[var(--color-foreground)]">{room.name}</span>
                      </span>
                      <Button variant="ghost" size="icon" onClick={() => addRoom(room.id)} title="Add room">
                        <Plus className="h-4 w-4 text-[var(--color-success)]" />
                      </Button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Close</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

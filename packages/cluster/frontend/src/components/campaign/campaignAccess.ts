import type { Room } from '@/hooks/useRooms'
import type { Participant } from '@/lib/participants'

interface Viewer {
  id: string
  is_admin: boolean
}

/** Whether *user* may start a goal (campaign) in *room* (#930, #940).
 *
 * Mirrors the server rule: a project room (not a DM) with a lead agent,
 * started by a room admin or owner or a global admin (``TASK_MANAGE`` in
 * ``api/v1/campaigns.py``). The server stays the authority; this only
 * decides what the UI offers. */
export function canStartCampaign(
  room: Room | null | undefined,
  participants: Record<string, Participant>,
  user: Viewer | null | undefined,
): boolean {
  if (!user || !room || !room.project_id || room.is_dm || !room.representative_agent_id) return false
  const myRole = Object.values(participants).find(p => p.user_id === user.id)?.role
  return user.is_admin || myRole === 'owner' || myRole === 'admin'
}

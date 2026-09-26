/**
 * RoomsPanel — extracted from AgentRoomsDialog (#158).
 *
 * Same assigned/available rooms UI rendered as a naked panel inside
 * AgentSettingsDialog. The legacy AgentRoomsDialog remains as a thin
 * Dialog wrapper around this component, because topology/DetailPanel
 * opens it independently for a focused "rooms only" intent.
 */
import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { apiFetch } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Plus, X } from 'lucide-react'
import { EntityAvatar } from '@/components/EntityAvatar'
import { useLocale } from '@/i18n/LocaleProvider'

interface RoomInfo { id: string; name: string; project_id: string }

interface Props {
  agentId: string | null
  /** Optional callback fired after every mutation so the parent can
   *  refresh its own state. */
  onChange?: () => void
}

export default function RoomsPanel({ agentId, onChange }: Props) {
  const { t } = useLocale()
  const scope = useMemo(() => ({ agentId, active: true, request: 0 }), [agentId])
  const currentScope = useRef(scope)
  currentScope.current = scope
  const [snapshot, setSnapshot] = useState<{ scope: typeof scope; assigned: RoomInfo[]; available: RoomInfo[]; loaded: boolean; loading: boolean; error: string | null; busy: boolean }>(
    () => ({ scope, assigned: [], available: [], loaded: false, loading: Boolean(agentId), error: null, busy: false }),
  )
  const isCurrent = useCallback(() => scope.active && currentScope.current === scope, [scope])

  const fetchRooms = useCallback(async () => {
    if (!agentId || !isCurrent()) return
    const request = ++scope.request
    const accepts = () => isCurrent() && scope.request === request
    setSnapshot(previous => ({ ...(previous.scope === scope ? previous : { scope, assigned: [], available: [], loaded: false, busy: false }), loading: true, error: null }))
    async function read<T>(path: string): Promise<T> {
      const response = await apiFetch(path)
      if (!response.ok) throw new Error(String(response.status))
      return response.json() as Promise<T>
    }
    try {
      const [rawAssigned, projects] = await Promise.all([
        read<Array<{ room_id: string; room_name: string; is_dm?: boolean }>>(`/api/v1/agents/${agentId}/rooms`),
        read<Array<{ id: string }>>('/api/v1/projects'),
      ])
      const assigned = rawAssigned.filter(room => !room.is_dm).map(room => ({ id: room.room_id, name: room.room_name, project_id: '' }))
      const allRooms = (await Promise.all(projects.map(project => read<RoomInfo[]>(`/api/v1/rooms?project_id=${project.id}&is_dm=false`)))).flat()
      const assignedIds = new Set(assigned.map(room => room.id))
      if (accepts()) setSnapshot(previous => ({ ...previous, scope, assigned, available: allRooms.filter(room => !assignedIds.has(room.id)), loaded: true, loading: false, error: null }))
    } catch {
      if (accepts()) setSnapshot(previous => ({ ...previous, loading: false, error: t('agentSetup.roomsLoadFailed') }))
    }
  }, [agentId, scope, isCurrent, t])

  useEffect(() => {
    scope.active = true
    void fetchRooms()
    return () => { scope.active = false }
  }, [scope, fetchRooms])

  const changeMembership = async (room: RoomInfo, add: boolean) => {
    if (!agentId || !isCurrent() || snapshot.scope !== scope || snapshot.loading || snapshot.busy) return
    ++scope.request
    setSnapshot(previous => ({ ...previous, busy: true, error: null }))
    try {
      const response = await apiFetch(`/api/v1/agents/${agentId}/rooms${add ? '' : `/${room.id}`}`, {
        method: add ? 'POST' : 'DELETE',
        ...(add ? { body: JSON.stringify({ room_id: room.id }) } : {}),
      })
      if (!isCurrent()) return
      if (!response.ok) {
        const body = await response.json().catch(() => ({}))
        throw new Error(typeof body.detail === 'string' ? body.detail : String(response.status))
      }
      // The membership write succeeded. Keep it visible even if reloading
      // the room inventory fails, and never notify a newly selected agent.
      setSnapshot(previous => ({ ...previous,
        assigned: add ? [...previous.assigned, room] : previous.assigned.filter(item => item.id !== room.id),
        available: add ? previous.available.filter(item => item.id !== room.id) : [...previous.available, room],
      }))
      onChange?.()
      await fetchRooms()
    } catch (error) {
      if (isCurrent()) setSnapshot(previous => ({ ...previous, error: t('agentSetup.roomsSaveFailed', { error: error instanceof Error ? error.message : String(error) }) }))
    } finally {
      if (isCurrent()) setSnapshot(previous => ({ ...previous, busy: false }))
    }
  }

  if (!agentId) return null
  const current = snapshot.scope === scope ? snapshot : { assigned: [], available: [], loaded: false, loading: true, error: null, busy: false }
  const assignedRooms = current.assigned
  const availableRooms = current.available
  const disabled = current.loading || current.busy

  return (
    <div className="space-y-5 py-2" data-testid="rooms-panel">
      <div className="flex items-center justify-between gap-3">
        {current.loading && <p role="status" className="text-sm text-[var(--color-foreground-muted)]">{t('admin.rooms.loading')}</p>}
        <Button variant="outline" size="sm" className="ml-auto" disabled={disabled} onClick={() => void fetchRooms()}>{current.error ? t('common.retry') : t('common.refresh')}</Button>
      </div>
      {current.error && <p role="alert" className="text-sm text-[var(--color-destructive)]">{current.error}</p>}
      {current.loaded && <>
      <div>
        <h3 className="text-badge uppercase text-[var(--color-foreground-muted)] mb-2 tracking-wider">
          {t('admin.rooms.assigned')}
        </h3>
        {assignedRooms.length === 0 ? (
          <p className="text-caption text-[var(--color-foreground-subtle)]">{t('admin.rooms.noneAssigned')}</p>
        ) : (
          <div className="space-y-2">
            {assignedRooms.map(room => (
              <div key={room.id} className="flex items-center justify-between rounded-[var(--radius-md)] bg-[var(--color-surface-alt)] border border-[var(--color-border)] px-3 py-1.5">
                <span className="flex items-center gap-2 min-w-0">
                  <EntityAvatar id={room.id} name={room.name} kind="room" size="sm" />
                  <span className="truncate text-sm font-medium text-[var(--color-foreground)]">{room.name}</span>
                </span>
                <Button
                  variant="ghost"
                  size="icon"
                  disabled={disabled}
                  onClick={() => void changeMembership(room, false)}
                  aria-label={`${t('admin.rooms.remove')}: ${room.name}`}
                  title={t('admin.rooms.remove')}
                >
                  <X className="h-4 w-4 text-[var(--color-warning)]" />
                </Button>
              </div>
            ))}
          </div>
        )}
      </div>
      <div>
        <h3 className="text-badge uppercase text-[var(--color-foreground-muted)] mb-2 tracking-wider">
          {t('admin.rooms.available')}
        </h3>
        {availableRooms.length === 0 ? (
          <p className="text-caption text-[var(--color-foreground-subtle)]">{t('admin.rooms.noneAvailable')}</p>
        ) : (
          <div className="space-y-2">
            {availableRooms.map(room => (
              <div key={room.id} className="flex items-center justify-between rounded-[var(--radius-md)] bg-[var(--color-surface-alt)] border border-[var(--color-border)] px-3 py-1.5">
                <span className="flex items-center gap-2 min-w-0">
                  <EntityAvatar id={room.id} name={room.name} kind="room" size="sm" />
                  <span className="truncate text-sm font-medium text-[var(--color-foreground)]">{room.name}</span>
                </span>
                <Button
                  variant="ghost"
                  size="icon"
                  disabled={disabled}
                  onClick={() => void changeMembership(room, true)}
                  aria-label={`${t('admin.rooms.add')}: ${room.name}`}
                  title={t('admin.rooms.add')}
                >
                  <Plus className="h-4 w-4 text-[var(--color-success)]" />
                </Button>
              </div>
            ))}
          </div>
        )}
      </div>
      </>}
    </div>
  )
}

import { useEffect, useRef, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { useFeedback } from '@/components/feedback/FeedbackProvider'
import { useLocale } from '@/i18n/LocaleProvider'
import { apiFetch } from '@/lib/api'

interface Attachment {
  id: string; agent_id: string; workspace_label: string; mode: 'read' | 'write'
  state: string; expires_at: string
  room_approved_by_user_id: string | null; global_approved_by_user_id: string | null
}
interface Participant { agent_id?: string; user_id?: string; role: string; display_name: string }
interface Props { open: boolean; onOpenChange: (open: boolean) => void; roomId: string; roomName: string }
const liveStates = new Set(['requested', 'machine_verified', 'active', 'revoking'])

async function read<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(path, init)
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : String(response.status))
  return data as T
}

/** Room approval works with room APIs alone, including for non-system administrators. */
export default function RoomWorkspaceDialog({ open, onOpenChange, roomId, roomName }: Props) {
  const { t, formatDate } = useLocale()
  const { confirm } = useFeedback()
  const current = useRef(0)
  const [rows, setRows] = useState<Attachment[]>([])
  const [participants, setParticipants] = useState<Participant[]>([])
  const [permissions, setPermissions] = useState({ room: false, global: false, manage: false })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [revision, setRevision] = useState(0)

  useEffect(() => {
    current.current += 1
    setRows([]); setParticipants([]); setPermissions({ room: false, global: false, manage: false }); setError(null); setBusy(null)
  }, [open, roomId])

  useEffect(() => {
    if (!open || !roomId) return
    let cancelled = false
    setLoading(true); setError(null)
    void Promise.all([
      read<{ id: string; is_admin: boolean }>('/api/v1/auth/me'),
      read<{ participants: Participant[]; archived_at?: string | null }>(`/api/v1/rooms/${roomId}`),
      read<Attachment[]>(`/api/v1/rooms/${roomId}/workspace-attachments`),
    ]).then(([viewer, room, attachments]) => {
      if (cancelled) return
      const self = room.participants.find(participant => participant.user_id === viewer.id)
      const canApprove = !room.archived_at && Boolean(self && ['owner', 'admin'].includes(self.role))
      setRows(attachments); setParticipants(room.participants)
      setPermissions({ room: canApprove, global: !room.archived_at && viewer.is_admin, manage: !room.archived_at && (canApprove || viewer.is_admin) })
    }).catch(() => { if (!cancelled) setError(t('agentSetup.workspaceLoadFailed')) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [open, roomId, revision, t])

  async function act(row: Attachment, suffix: string, method = 'POST') {
    const actionContext = current.current
    setBusy(row.id); setError(null)
    try {
      await read(`/api/v1/rooms/${roomId}/workspace-attachments/${row.id}${suffix}`, { method })
      if (current.current === actionContext) setRevision(value => value + 1)
    } catch (cause) {
      if (current.current === actionContext) setError(t('agentSetup.workspaceActionFailed', { error: cause instanceof Error ? cause.message : String(cause) }))
    } finally { if (current.current === actionContext) setBusy(null) }
  }

  const labels: Record<string, string> = {
    requested: t('agentSetup.workspaceStatus.requested'), active: t('agentSetup.workspaceStatus.active'),
    machine_verified: t('agentSetup.workspaceStatus.machine_verified'), revoking: t('agentSetup.workspaceStatus.revoking'),
    revoked: t('agentSetup.workspaceStatus.revoked'), expired: t('agentSetup.workspaceStatus.expired'), failed: t('agentSetup.workspaceStatus.failed'),
  }

  return <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent className="max-w-xl">
      <DialogHeader><DialogTitle>{t('agentSetup.workspaceRoomDialogTitle', { room: roomName })}</DialogTitle><DialogDescription>{t('agentSetup.workspaceRoomDialogDescription')}</DialogDescription></DialogHeader>
      <div className="space-y-3">
        <Button variant="outline" size="sm" disabled={loading || Boolean(busy)} onClick={() => setRevision(value => value + 1)}><RefreshCw className={loading ? 'animate-spin' : ''} />{t('common.refresh')}</Button>
        {loading && <p role="status" className="text-sm text-[var(--color-foreground-muted)]">{t('agentSetup.workspaceLoading')}</p>}
        {error && <p role="alert" className="text-sm text-[var(--color-destructive)]">{error}</p>}
        {!loading && !error && rows.length === 0 && <p className="text-sm text-[var(--color-foreground-muted)]">{t('agentSetup.workspaceRoomEmpty')}</p>}
        {rows.map(row => {
          const expired = new Date(row.expires_at).getTime() <= Date.now()
          const pending = row.state === 'requested' && !expired
          const agentName = participants.find(participant => participant.agent_id === row.agent_id)?.display_name ?? row.agent_id
          return <section key={row.id} className="space-y-3 rounded-[var(--radius-md)] border border-[var(--color-border)] p-3">
            <div className="flex flex-wrap items-center justify-between gap-2"><span className="text-sm font-medium">{row.workspace_label}</span><span className="text-xs text-[var(--color-foreground-muted)]">{expired && liveStates.has(row.state) ? labels.expired : labels[row.state] ?? row.state}</span></div>
            <p className="text-xs text-[var(--color-foreground-muted)]">{agentName} · {row.mode === 'read' ? t('agentSetup.workspaceRead') : t('agentSetup.workspaceWrite')}</p>
            <p className="text-xs text-[var(--color-foreground-muted)]">{t('agentSetup.workspaceExpires', { date: formatDate(new Date(row.expires_at), { dateStyle: 'short', timeStyle: 'short' }) })}</p>
            {pending && <>
              <div className="grid gap-2 text-xs sm:grid-cols-2"><p>{t('agentSetup.workspaceRoomApproval')}: {row.room_approved_by_user_id ? t('agentSetup.workspaceApprovalDone') : t('agentSetup.workspaceApprovalPending')}</p><p>{t('agentSetup.workspaceGlobalApproval')}: {row.global_approved_by_user_id ? t('agentSetup.workspaceApprovalDone') : t('agentSetup.workspaceApprovalPending')}</p></div>
              <div className="flex flex-wrap gap-2">
                {permissions.room && !row.room_approved_by_user_id && <Button variant="outline" size="sm" disabled={Boolean(busy) || loading} onClick={() => void act(row, '/approve-room')}>{t('agentSetup.workspaceApproveRoom')}</Button>}
                {permissions.global && !row.global_approved_by_user_id && <Button variant="outline" size="sm" disabled={Boolean(busy) || loading} onClick={() => void act(row, '/approve-global')}>{t('agentSetup.workspaceApproveGlobal')}</Button>}
              </div>
            </>}
            {permissions.manage && liveStates.has(row.state) && row.state !== 'revoking' && <Button variant="outline" size="sm" disabled={Boolean(busy) || loading} onClick={async () => {
              const revokeContext = current.current
              if (await confirm({ title: t('agentSetup.workspaceRevoke'), description: t('agentSetup.workspaceRevokeConfirm'), destructive: true }) && current.current === revokeContext) await act(row, '', 'DELETE')
            }}>{t('agentSetup.workspaceRevoke')}</Button>}
          </section>
        })}
      </div>
      <DialogFooter><Button onClick={() => onOpenChange(false)}>{t('common.close')}</Button></DialogFooter>
    </DialogContent>
  </Dialog>
}

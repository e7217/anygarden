import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { ExternalLink, FolderOpen, Loader2, Plus, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { useFeedback } from '@/components/feedback/FeedbackProvider'
import { apiFetch } from '@/lib/api'
import { useLocale } from '@/i18n/LocaleProvider'
import { workspaceCliPrefix, workspaceSupportMessage, type WorkspaceOptions } from '@/lib/workspaceAttachments'

interface Attachment {
  id: string; agent_id: string; room_id: string; workspace_id: string; machine_id: string
  workspace_label: string; mode: 'read' | 'write'; state: string; expires_at: string
  room_approved_by_user_id?: string | null; global_approved_by_user_id?: string | null
  failure_code?: string | null; roomName: string
}
interface Participant { id: string; user_id?: string; agent_id?: string; role: string }
interface RoomScope { id: string; name: string; participantId: string; canManage: boolean; canApprove: boolean }
interface Viewer { id: string; is_admin: boolean }

async function readJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(path, init)
  const body = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : String(response.status))
  return body as T
}
const liveStates = new Set(['requested', 'machine_verified', 'active', 'revoking'])
const shellQuote = (value: string) => `'${value.replace(/'/g, `'"'"'`)}'`

/** Room, system, and local approvals remain separate; only the server can activate a lease. */
export default function WorkspacePanel({ agentId, onNavigateAway }: { agentId: string | null; onNavigateAway: () => void }) {
  const { t, formatDate } = useLocale()
  const { confirm } = useFeedback()
  const currentAgent = useRef(0)
  const [attachments, setAttachments] = useState<Attachment[]>([])
  const [rooms, setRooms] = useState<RoomScope[]>([])
  const [viewer, setViewer] = useState<Viewer | null>(null)
  const [placed, setPlaced] = useState(false)
  const [loading, setLoading] = useState(false)
  const [failed, setFailed] = useState(false)
  const [options, setOptions] = useState<Record<string, WorkspaceOptions>>({})
  const [partial, setPartial] = useState(false)
  const [revision, setRevision] = useState(0)
  const [formOpen, setFormOpen] = useState(false)
  const [roomId, setRoomId] = useState('')
  const [workspaceId, setWorkspaceId] = useState('')
  const [mode, setMode] = useState<'read' | 'write'>('read')
  const [duration, setDuration] = useState('3600')
  const [proofs, setProofs] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  useEffect(() => {
    currentAgent.current += 1
    setFormOpen(false); setRoomId(''); setWorkspaceId(''); setProofs({}); setError(null); setNotice(null)
    setAttachments([]); setRooms([]); setViewer(null); setBusy(null); setPartial(false); setOptions({})
  }, [agentId])

  useEffect(() => {
    if (!agentId) return
    let cancelled = false
    setLoading(true); setFailed(false)
    async function load() {
      const [currentViewer, agent, assigned] = await Promise.all([
        readJson<Viewer>('/api/v1/auth/me'),
        readJson<{ placed_on_machine_id: string | null }>(`/api/v1/agents/${agentId}`),
        readJson<Array<{ room_id: string; room_name: string }>>(`/api/v1/agents/${agentId}/rooms`),
      ])
      const roomResults = await Promise.allSettled(assigned.map(async room => {
        const [detail, rows] = await Promise.all([
          readJson<{ participants: Participant[]; archived_at?: string | null }>(`/api/v1/rooms/${room.room_id}`),
          readJson<Attachment[]>(`/api/v1/rooms/${room.room_id}/workspace-attachments`),
        ])
        const user = detail.participants.find(participant => participant.user_id === currentViewer.id)
        const agentParticipant = detail.participants.find(participant => participant.agent_id === agentId && ['member', 'admin', 'owner'].includes(participant.role))
        const canApprove = Boolean(user && ['admin', 'owner'].includes(user.role) && !detail.archived_at)
        const support = await readJson<WorkspaceOptions>(`/api/v1/rooms/${room.room_id}/workspace-attachments/options?agent_id=${encodeURIComponent(agentId!)}`).catch(() => null)
        return {
          support,
          scope: { id: room.room_id, name: room.room_name, participantId: agentParticipant?.id ?? '', canApprove, canManage: !detail.archived_at && (currentViewer.is_admin || canApprove) },
          attachments: rows.filter(row => row.agent_id === agentId).map(row => ({ ...row, roomName: room.room_name })),
        }
      }))
      if (cancelled) return
      const successes = roomResults.flatMap(result => result.status === 'fulfilled' ? [result.value] : [])
      setViewer(currentViewer); setPlaced(Boolean(agent.placed_on_machine_id))
      setOptions(Object.fromEntries(successes.filter(result => result.support).map(result => [result.scope.id, result.support!])))
      setRooms(successes.map(result => result.scope)); setAttachments(successes.flatMap(result => result.attachments))
      setPartial(roomResults.some(result => result.status === 'rejected'))
    }
    void load().catch(() => { if (!cancelled) setFailed(true) }).finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [agentId, revision])

  const eligibleRooms = rooms.filter(room => room.canManage && room.participantId)
  const selectedRoom = eligibleRooms.find(room => room.id === roomId)
  const currentOptions = roomId ? options[roomId] : Object.values(options)[0]
  const catalog = currentOptions?.workspaces ?? []
  const canRead = currentOptions?.read.supported === true
  const canWrite = currentOptions?.write.supported === true
  const selectedWorkspace = catalog.find(workspace => workspace.workspace_id === workspaceId)
  // The API reserves the agent while any persisted live state remains, even if its deadline passed.
  // Keep revoke available so an expired, unreconciled lease can be replaced explicitly.
  const hasConnection = attachments.some(row => liveStates.has(row.state))
  const stateLabels: Record<string, string> = {
    active: t('agentSetup.workspaceStatus.active'), requested: t('agentSetup.workspaceStatus.requested'),
    approved: t('agentSetup.workspaceStatus.approved'), verified: t('agentSetup.workspaceStatus.verified'),
    activating: t('agentSetup.workspaceStatus.activating'), revoked: t('agentSetup.workspaceStatus.revoked'),
    expired: t('agentSetup.workspaceStatus.expired'), failed: t('agentSetup.workspaceStatus.failed'),
    machine_verified: t('agentSetup.workspaceStatus.machine_verified'), revoking: t('agentSetup.workspaceStatus.revoking'),
  }

  async function action(key: string, path: string, method = 'POST', body?: unknown) {
    const actionAgent = currentAgent.current
    setBusy(key); setError(null); setNotice(null)
    try {
      await readJson(path, { method, ...(body ? { body: JSON.stringify(body) } : {}) })
      if (currentAgent.current !== actionAgent) return false
      setRevision(value => value + 1)
      return true
    } catch (cause) {
      if (currentAgent.current !== actionAgent) return false
      setError(t('agentSetup.workspaceActionFailed', { error: cause instanceof Error ? cause.message : String(cause) }))
      // A rejected verification can still change the server's lease state.
      setRevision(value => value + 1)
      return false
    } finally { if (currentAgent.current === actionAgent) setBusy(null) }
  }

  return <div className="space-y-4" data-testid="workspace-panel">
    <div className="flex items-start gap-3">
      <FolderOpen className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-foreground-muted)]" />
      <div className="space-y-1"><p className="text-sm font-medium">{t('agentSetup.managedWorkspace')}</p><p className="text-sm leading-relaxed text-[var(--color-foreground-muted)]">{t('agentSetup.managedWorkspaceHint')}</p></div>
    </div>
    <div className="space-y-3 border-t border-[var(--color-border)] pt-4">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-medium">{t('agentSetup.externalWorkspaces')}</h3>
        <Button variant="ghost" size="sm" disabled={loading || Boolean(busy)} onClick={() => setRevision(value => value + 1)}><RefreshCw className={loading ? 'animate-spin' : ''} />{t('common.refresh')}</Button>
      </div>
      <p className="text-xs leading-relaxed text-[var(--color-foreground-muted)]">{t('agentSetup.externalWorkspaceHint')}</p>
      {loading && <p role="status" className="text-sm text-[var(--color-foreground-muted)]">{t('agentSetup.workspaceLoading')}</p>}
      {failed && <p role="alert" className="text-sm text-[var(--color-destructive)]">{t('agentSetup.workspaceLoadFailed')}</p>}
      {partial && <p role="alert" className="text-xs text-[var(--color-warning)]">{t('agentSetup.workspacePartialLoad')}</p>}
      {error && <p role="alert" className="text-sm text-[var(--color-destructive)]">{error}</p>}
      {notice && <p role="status" className="text-sm text-[var(--color-foreground-muted)]">{notice}</p>}
      {!loading && !failed && <>
        {attachments.length === 0 && <p className="text-sm text-[var(--color-foreground-muted)]">{t('agentSetup.noWorkspaces')}</p>}
        {attachments.map(attachment => {
          const room = rooms.find(scope => scope.id === attachment.room_id)
          const base = `/api/v1/rooms/${attachment.room_id}/workspace-attachments/${attachment.id}`
          const approved = Boolean(attachment.room_approved_by_user_id && attachment.global_approved_by_user_id)
          const requested = attachment.state === 'requested'
          const expired = new Date(attachment.expires_at).getTime() <= Date.now()
          const support = options[attachment.room_id]
          const canVerify = support?.machine_id === attachment.machine_id && support?.[attachment.mode].supported === true
          const localCommand = `${workspaceCliPrefix(support?.execution_kind ?? null, support?.node_data_dir)} consent ${shellQuote(attachment.workspace_id)} \\\n  --agent-id ${shellQuote(attachment.agent_id)} \\\n  --room-id ${shellQuote(attachment.room_id)} --mode ${attachment.mode}`
          return <section key={attachment.id} className="space-y-3 rounded-[var(--radius-md)] border border-[var(--color-border)] p-3">
            <div className="flex flex-wrap items-center justify-between gap-2"><span className="break-words text-sm font-medium">{attachment.workspace_label}</span><span className="text-xs text-[var(--color-foreground-muted)]">{expired && liveStates.has(attachment.state) ? t('agentSetup.workspaceStatus.expired') : stateLabels[attachment.state] ?? attachment.state} · {attachment.mode === 'write' ? t('agentSetup.workspaceWrite') : t('agentSetup.workspaceRead')}</span></div>
            <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-[var(--color-foreground-muted)]">
              <span>{t('agentSetup.workspaceExpires', { date: formatDate(new Date(attachment.expires_at), { dateStyle: 'short', timeStyle: 'short' }) })}</span>
              <Link to={`/rooms/${attachment.room_id}`} onClick={onNavigateAway} className="inline-flex min-h-[var(--control-sm-height)] items-center gap-1 rounded px-1 text-[var(--color-brand-text)] hover:underline" aria-label={`${t('agentSetup.workspaceRoom')}: ${attachment.roomName}`}>{attachment.roomName}<ExternalLink className="h-3 w-3" /></Link>
            </div>
            {attachment.failure_code && <p className="break-all text-xs text-[var(--color-destructive)]">{workspaceSupportMessage(attachment.failure_code, t)}</p>}
            {requested && !expired && <>
              <div className="grid gap-2 text-xs sm:grid-cols-2">
                <p>{t('agentSetup.workspaceRoomApproval')}: {attachment.room_approved_by_user_id ? t('agentSetup.workspaceApprovalDone') : t('agentSetup.workspaceApprovalPending')}</p>
                <p>{t('agentSetup.workspaceGlobalApproval')}: {attachment.global_approved_by_user_id ? t('agentSetup.workspaceApprovalDone') : t('agentSetup.workspaceApprovalPending')}</p>
              </div>
              <div className="flex flex-wrap gap-2">
                {!attachment.room_approved_by_user_id && room?.canApprove && <Button variant="outline" size="sm" disabled={Boolean(busy)} onClick={() => void action(attachment.id, `${base}/approve-room`)}>{t('agentSetup.workspaceApproveRoom')}</Button>}
                {!attachment.global_approved_by_user_id && viewer?.is_admin && <Button variant="outline" size="sm" disabled={Boolean(busy)} onClick={() => void action(attachment.id, `${base}/approve-global`)}>{t('agentSetup.workspaceApproveGlobal')}</Button>}
              </div>
              {approved && viewer?.is_admin && !canVerify && <p className="text-xs leading-relaxed text-[var(--color-foreground-muted)]">{workspaceSupportMessage(support?.[attachment.mode].reason, t)}</p>}
              {approved && viewer?.is_admin && canVerify && <div className="space-y-2 border-t border-[var(--color-border)] pt-3">
                <p className="text-xs leading-relaxed text-[var(--color-foreground-muted)]">{t('agentSetup.workspaceConsentHint')}</p>
                {support?.execution_kind === 'integrated' && <p className="text-xs leading-relaxed text-[var(--color-foreground-muted)]">{t('agentSetup.workspaceNodeRegistryHint')}</p>}
                <pre className="overflow-x-auto rounded-[var(--radius-sm)] bg-[var(--color-surface-alt)] p-3 text-xs"><code>{localCommand}</code></pre>
                <Label htmlFor={`workspace-proof-${attachment.id}`}>{t('agentSetup.workspaceConsent')}</Label>
                <Input id={`workspace-proof-${attachment.id}`} type="password" autoComplete="off" placeholder="wcp_…" value={proofs[attachment.id] ?? ''} onChange={event => setProofs(previous => ({ ...previous, [attachment.id]: event.target.value }))} />
                <Button size="sm" disabled={Boolean(busy) || !/^wcp_[0-9a-f]{64}$/.test(proofs[attachment.id] ?? '')} onClick={async () => {
                  if (await action(attachment.id, `${base}/verify`, 'POST', { consent_proof: proofs[attachment.id] })) {
                    setProofs(previous => ({ ...previous, [attachment.id]: '' })); setNotice(t('agentSetup.workspaceVerificationSent'))
                  }
                }}>{busy === attachment.id && <Loader2 className="animate-spin" />}{t('agentSetup.workspaceVerify')}</Button>
              </div>}
            </>}
            {liveStates.has(attachment.state) && attachment.state !== 'revoking' && room?.canManage && <Button variant="outline" size="sm" disabled={Boolean(busy)} onClick={async () => {
              const revokeContext = currentAgent.current
              if (await confirm({ title: t('agentSetup.workspaceRevoke'), description: t('agentSetup.workspaceRevokeConfirm'), destructive: true }) && currentAgent.current === revokeContext) await action(attachment.id, base, 'DELETE')
            }}>{t('agentSetup.workspaceRevoke')}</Button>}
          </section>
        })}
        {!placed ? <p className="text-xs text-[var(--color-foreground-muted)]">{t('agentSetup.workspaceNotPlaced')}</p>
          : hasConnection ? <p className="text-xs text-[var(--color-foreground-muted)]">{t('agentSetup.workspaceExisting')}</p>
            : eligibleRooms.length === 0 ? <p className="text-xs text-[var(--color-foreground-muted)]">{t('agentSetup.workspaceNoEligibleRooms')}</p>
            : !canRead && !canWrite && !formOpen ? <div className="space-y-2 rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-surface-alt)] p-3 text-sm text-[var(--color-foreground-muted)]"><p>{workspaceSupportMessage(currentOptions?.read.reason, t)}</p><p className="text-xs">{t('agentSetup.workspaceWriteUnavailable')}</p></div>
            : <>
              <Button variant="outline" size="sm" disabled={partial} aria-expanded={formOpen} onClick={() => { setFormOpen(!formOpen); setRoomId(eligibleRooms[0]?.id ?? ''); setWorkspaceId(catalog[0]?.workspace_id ?? ''); setMode('read') }}><Plus />{t('agentSetup.workspaceConnect')}</Button>
              {formOpen && <div className="space-y-4 rounded-[var(--radius-md)] border border-[var(--color-border)] p-4">
                <p className="text-xs leading-relaxed text-[var(--color-foreground-muted)]">{t('agentSetup.workspaceRequestHint')}</p>
                {eligibleRooms.length === 0 && <p className="text-sm text-[var(--color-foreground-muted)]">{t('agentSetup.workspaceNoEligibleRooms')}</p>}
                <div className="space-y-2"><Label htmlFor="workspace-room">{t('agentSetup.workspaceSelectRoom')}</Label><Select id="workspace-room" value={roomId} onChange={event => { setRoomId(event.target.value); setWorkspaceId(options[event.target.value]?.workspaces[0]?.workspace_id ?? ''); setMode('read') }}>{eligibleRooms.map(room => <option key={room.id} value={room.id}>{room.name}</option>)}</Select></div>
                {!canRead && !canWrite ? <p role="alert" className="text-sm text-[var(--color-foreground-muted)]">{workspaceSupportMessage(currentOptions?.read.reason, t)}</p> : catalog.length === 0 ? <div className="space-y-2"><p className="text-sm text-[var(--color-foreground-muted)]">{t('agentSetup.workspaceNoCatalog')}</p><details className="text-xs"><summary className="cursor-pointer py-2 font-medium">{t('agentSetup.workspaceLocalRegistration')}</summary><p className="mb-2 leading-relaxed text-[var(--color-foreground-muted)]">{t('agentSetup.workspaceRegistrationHint')}</p>{currentOptions?.execution_kind === 'integrated' && <p className="mb-2 leading-relaxed text-[var(--color-foreground-muted)]">{t('agentSetup.workspaceNodeRegistryHint')}</p>}<pre className="overflow-x-auto rounded-[var(--radius-sm)] bg-[var(--color-surface-alt)] p-3"><code>{`${workspaceCliPrefix(currentOptions?.execution_kind ?? null, currentOptions?.node_data_dir)} register /path/to/project \\\n  --label project --max-mode read --allow src\n${workspaceCliPrefix(currentOptions?.execution_kind ?? null, currentOptions?.node_data_dir)} list`}</code></pre></details></div>
                  : eligibleRooms.length > 0 && <>
                    <div className="space-y-2"><Label htmlFor="workspace-catalog">{t('agentSetup.workspaceSelectFolder')}</Label><Select id="workspace-catalog" value={workspaceId} onChange={event => { setWorkspaceId(event.target.value); setMode('read') }}>{catalog.map(workspace => <option key={workspace.workspace_id} value={workspace.workspace_id}>{workspace.label}</option>)}</Select></div>
                    <div className="grid gap-4 sm:grid-cols-2"><div className="space-y-2"><Label htmlFor="workspace-mode">{t('agentSetup.workspaceAccess')}</Label><Select id="workspace-mode" value={mode} onChange={event => setMode(event.target.value as 'read' | 'write')}>{canRead && <option value="read">{t('agentSetup.workspaceRead')}</option>}{canWrite && selectedWorkspace?.max_mode === 'write' && <option value="write">{t('agentSetup.workspaceWrite')}</option>}</Select></div><div className="space-y-2"><Label htmlFor="workspace-duration">{t('agentSetup.workspaceDuration')}</Label><Select id="workspace-duration" value={duration} onChange={event => setDuration(event.target.value)}><option value="900">{t('agentSetup.workspace15min')}</option><option value="3600">{t('agentSetup.workspace1hour')}</option><option value="86400">{t('agentSetup.workspace24hours')}</option></Select></div></div>
                    <Button size="sm" disabled={Boolean(busy) || !selectedRoom || !selectedWorkspace || !currentOptions?.[mode].supported || (mode === 'write' && selectedWorkspace.max_mode !== 'write')} onClick={async () => {
                      if (!selectedRoom || !selectedWorkspace || !currentOptions?.[mode].supported) return
                      if (await action('create', `/api/v1/rooms/${selectedRoom.id}/workspace-attachments`, 'POST', { agent_id: agentId, participant_id: selectedRoom.participantId, workspace_id: selectedWorkspace.workspace_id, mode, expires_in_seconds: Number(duration) })) setFormOpen(false)
                    }}>{busy === 'create' && <Loader2 className="animate-spin" />}{t('agentSetup.workspaceRequest')}</Button>
                  </>}
              </div>}
            </>}
      </>}
    </div>
  </div>
}

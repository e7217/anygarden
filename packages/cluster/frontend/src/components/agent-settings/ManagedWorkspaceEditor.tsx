import { useEffect, useRef, useState } from 'react'
import { FilePlus2, FolderPlus, Save, Upload } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import type { ManagedWorkspaceResult } from '@/hooks/useManagedWorkspace'
import { useLocale } from '@/i18n/LocaleProvider'
import { apiFetch } from '@/lib/api'

const MAX_UPLOAD_BYTES = 1024 * 1024
const MAX_TEXT_BYTES = 64 * 1024

function base64File(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(new Error('read_failed'))
    reader.onload = () => resolve(String(reader.result).split(',', 2)[1] ?? '')
    reader.readAsDataURL(file)
  })
}

type Props = {
  agentId: string
  folder: string
  filePath: string | null
  folderData: ManagedWorkspaceResult
  fileData: ManagedWorkspaceResult | null
  reloadFolder: () => Promise<void>
  reloadFile: (path: string) => Promise<void>
}

export default function ManagedWorkspaceEditor({ agentId, folder, filePath, folderData, fileData, reloadFolder, reloadFile }: Props) {
  const { t } = useLocale()
  const [mode, setMode] = useState<'folder' | 'file' | null>(null)
  const [name, setName] = useState('')
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const uploadRef = useRef<HTMLInputElement>(null)
  const requestRef = useRef<AbortController | null>(null)
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    return () => { alive.current = false; requestRef.current?.abort() }
  }, [])
  useEffect(() => {
    if (filePath !== null && fileData?.snapshot?.preview_status === 'text') {
      setDraft(fileData.snapshot.text ?? '')
    }
  }, [filePath, fileData?.snapshot?.sha256, fileData?.snapshot?.preview_status])

  const token = (filePath !== null ? fileData?.snapshot?.edit_token : null) ?? folderData.snapshot?.edit_token
  const pathFor = (leaf: string) => folder ? `${folder}/${leaf}` : leaf
  const message = (reason: string) => {
    const known: Record<string, Parameters<typeof t>[0]> = {
      conflict: 'workspace.managed.conflict', stale: 'workspace.managed.staleEdit',
      too_large: 'workspace.managed.uploadTooLarge', blocked: 'workspace.managed.blocked',
      not_found: 'workspace.managed.notFound', unsupported: 'workspace.managed.editUnavailable',
      offline: 'workspace.managed.offline', timeout: 'workspace.managed.timeout',
      busy: 'workspace.managed.busy', not_ready: 'workspace.managed.notReady',
    }
    return t(known[reason] ?? 'workspace.managed.writeFailed')
  }
  const validName = (value: string) => value.length > 0 && value.length <= 255 && !['.', '..'].includes(value) && !/[\\/\x00-\x1f\x7f]/.test(value)

  async function change(endpoint: 'folder' | 'upload' | 'file', method: 'POST' | 'PUT', body: Record<string, string>, refreshFile = false) {
    if (!token || busy) return false
    const abort = new AbortController()
    requestRef.current?.abort()
    requestRef.current = abort
    setBusy(true); setError(null); setNotice(null)
    try {
      const response = await apiFetch(`/api/v1/agents/${encodeURIComponent(agentId)}/workspace/${endpoint}`, {
        method, signal: abort.signal, body: JSON.stringify({ ...body, edit_token: token }),
      })
      const result = await response.json().catch(() => ({})) as { status?: string }
      if (!alive.current || abort.signal.aborted) return false
      if (!response.ok || result.status !== 'ready') {
        throw new Error(response.status === 409 ? 'conflict' : result.status ?? 'failed')
      }
      if (refreshFile && filePath) await reloadFile(filePath)
      else await reloadFolder()
      if (alive.current && !abort.signal.aborted) {
        setMode(null); setName(''); setNotice(t('workspace.managed.saved'))
      }
      return true
    } catch (cause) {
      if (alive.current && !abort.signal.aborted) setError(message(cause instanceof Error ? cause.message : 'failed'))
      return false
    } finally {
      if (alive.current && !abort.signal.aborted) setBusy(false)
    }
  }

  async function upload(file: File) {
    if (file.size > MAX_UPLOAD_BYTES) { setError(message('too_large')); return }
    if (!validName(file.name)) { setError(message('blocked')); return }
    try {
      const content_base64 = await base64File(file)
      if (!alive.current) return
      await change('upload', 'POST', { path: pathFor(file.name), content_base64 })
    } catch {
      if (alive.current) setError(message('failed'))
    }
  }

  const editable = filePath !== null && fileData?.status === 'ready' && fileData.snapshot?.preview_status === 'text' && Boolean(fileData.snapshot.sha256)
  const changed = editable && draft !== fileData?.snapshot?.text
  return <div className="space-y-3" data-testid="managed-workspace-editor">
    {filePath === null && <>
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" size="sm" className="min-h-11 sm:min-h-8" disabled={busy} onClick={() => { setMode('folder'); setName(''); setError(null) }}><FolderPlus />{t('workspace.managed.newFolder')}</Button>
        <Button variant="outline" size="sm" className="min-h-11 sm:min-h-8" disabled={busy} onClick={() => { setMode('file'); setName(''); setDraft(''); setError(null) }}><FilePlus2 />{t('workspace.managed.newFile')}</Button>
        <Button variant="outline" size="sm" className="min-h-11 sm:min-h-8" disabled={busy} onClick={() => uploadRef.current?.click()}><Upload />{t('workspace.managed.upload')}</Button>
        <input ref={uploadRef} className="sr-only" type="file" aria-label={t('workspace.managed.upload')} onChange={(event) => {
          const file = event.target.files?.[0]
          event.target.value = ''
          if (file) void upload(file)
        }} />
      </div>
      {mode && <div className="space-y-2 rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-surface-alt)] p-3">
        <label className="block space-y-1 text-sm"><span>{mode === 'folder' ? t('workspace.managed.folderName') : t('workspace.managed.fileName')}</span><Input value={name} maxLength={255} onChange={event => setName(event.target.value)} disabled={busy} /></label>
        {mode === 'file' && <label className="block space-y-1 text-sm"><span>{t('workspace.managed.editContent')}</span><Textarea value={draft} rows={9} onChange={event => setDraft(event.target.value)} disabled={busy} className="min-h-44 font-mono" /></label>}
        <div className="flex flex-wrap gap-2">
          <Button size="sm" className="min-h-11 sm:min-h-8" disabled={busy || !validName(name) || (mode === 'file' && new TextEncoder().encode(draft).length > MAX_TEXT_BYTES)} onClick={() => void change(mode, mode === 'file' ? 'PUT' : 'POST', mode === 'file' ? { path: pathFor(name), text: draft, expected_sha256: 'absent' } : { path: pathFor(name) })}>{t('workspace.managed.create')}</Button>
          <Button variant="outline" size="sm" className="min-h-11 sm:min-h-8" disabled={busy} onClick={() => { setMode(null); setError(null) }}>{t('common.cancel')}</Button>
        </div>
      </div>}
    </>}
    {editable && <div className="space-y-2">
      <label className="block space-y-1 text-sm"><span>{t('workspace.managed.editContent')}</span><Textarea aria-label={t('workspace.managed.editContent')} className="min-h-56 font-mono" value={draft} onChange={event => setDraft(event.target.value)} disabled={busy} /></label>
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" className="min-h-11 sm:min-h-8" disabled={busy || !changed || new TextEncoder().encode(draft).length > MAX_TEXT_BYTES} onClick={() => void change('file', 'PUT', { path: filePath!, text: draft, expected_sha256: fileData!.snapshot!.sha256! }, true)}><Save />{t('common.save')}</Button>
        {changed && <span className="text-xs text-[var(--color-foreground-muted)]">{t('workspace.managed.unsaved')}</span>}
        {new TextEncoder().encode(draft).length > MAX_TEXT_BYTES && <span className="text-xs text-[var(--color-destructive)]">{t('workspace.managed.textTooLarge')}</span>}
      </div>
    </div>}
    {error && <p role="alert" className="text-sm text-[var(--color-destructive)]">{error}</p>}
    {notice && <p role="status" className="text-sm text-[var(--color-foreground-muted)]">{notice}</p>}
  </div>
}

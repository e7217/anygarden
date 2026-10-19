import { useEffect, useRef, useState, type DragEvent } from 'react'
import { ArrowUp, Download, FileArchive, FileText, Folder, FolderOpen, Link2, Loader2, Pencil, RefreshCw, Trash2, Upload } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useFeedback } from '@/components/feedback/FeedbackProvider'
import { useManagedWorkspace, type WorkspaceEntry } from '@/hooks/useManagedWorkspace'
import { useLocale } from '@/i18n/LocaleProvider'
import ManagedWorkspaceEditor from './ManagedWorkspaceEditor'
import {
  changeWorkspace,
  downloadWorkspaceArchive,
  downloadWorkspaceFile,
  fetchWorkspaceFile,
  isImagePath,
  uploadWorkspaceFiles,
  validEntryName,
  type TransferLimits,
  type UploadOutcome,
} from './workspaceFiles'

function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(bytes >= 10 * 1024 * 1024 ? 0 : 1)} MiB`
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KiB`
  return `${bytes} B`
}

export default function ManagedWorkspacePanel({ agentId }: { agentId: string | null }) {
  const { t, formatDate } = useLocale()
  const { confirm } = useFeedback()
  const workspace = useManagedWorkspace(agentId)
  const folder = workspace.folderData
  const file = workspace.fileData
  const result = file ?? folder
  const snapshot = result?.snapshot
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [renaming, setRenaming] = useState<{ name: string; value: string } | null>(null)
  const [dragging, setDragging] = useState(false)
  const [uploads, setUploads] = useState<UploadOutcome[]>([])
  const [imageUrl, setImageUrl] = useState<string | null>(null)
  const [progress, setProgress] = useState<{ name: string; loaded: number; total: number | null } | null>(null)
  const dragDepth = useRef(0)

  const token = folder?.status === 'ready' ? folder.snapshot?.edit_token ?? null : null
  const canEdit = Boolean(folder?.can_edit && token)
  const canManage = Boolean(folder?.can_manage_files && token)
  // #914 — large files and folder zips over HTTP on machines that support it.
  const limits: TransferLimits | null = folder?.can_transfer && folder.transfer_max_bytes && folder.archive_max_bytes
    ? { transferMax: folder.transfer_max_bytes, archiveMax: folder.archive_max_bytes }
    : null
  const trackProgress = (name: string) => (loaded: number, total: number | null) => setProgress({ name, loaded, total })
  const canPreviewImage = Boolean(
    agentId && workspace.filePath && file?.can_manage_files && file.status === 'ready'
      && file.snapshot?.preview_status !== 'text' && isImagePath(workspace.filePath),
  )

  useEffect(() => {
    setImageUrl(null)
    if (!canPreviewImage || !agentId || !workspace.filePath) return
    const abort = new AbortController()
    let url: string | null = null
    fetchWorkspaceFile(agentId, workspace.filePath, abort.signal)
      .then(blob => { url = URL.createObjectURL(blob); setImageUrl(url) })
      .catch(() => {})
    return () => { abort.abort(); if (url) URL.revokeObjectURL(url) }
  }, [canPreviewImage, agentId, workspace.filePath])

  useEffect(() => { setUploads([]); setRenaming(null); setActionError(null) }, [agentId, workspace.folder])

  const statusMessage = (status: string) => {
    const messages: Record<string, string> = {
      not_placed: t('workspace.managed.notPlaced'), offline: t('workspace.managed.offline'),
      unsupported: t('workspace.managed.unsupported'), not_ready: t('workspace.managed.notReady'),
      stale: t('workspace.managed.stale'), blocked: t('workspace.managed.blocked'),
      not_found: t('workspace.managed.notFound'), too_many_entries: t('workspace.managed.tooMany'),
      timeout: t('workspace.managed.timeout'), busy: t('workspace.managed.busy'),
      conflict: t('workspace.managed.conflict'),
      too_large: limits ? t('workspace.managed.tooLargeLimit', { limit: formatBytes(limits.transferMax) }) : t('workspace.managed.uploadTooLarge'),
    }
    return messages[status] ?? t('workspace.managed.writeFailed')
  }
  const permission: Record<string, string> = {
    restricted: t('workspace.managed.restricted'), standard: t('workspace.managed.standard'), trusted: t('workspace.managed.trusted'),
  }
  const agentStates: Record<string, string> = {
    running: t('admin.agentSettings.state.running'), starting: t('admin.agentSettings.state.starting'),
    stopping: t('admin.agentSettings.state.stopping'), stopped: t('admin.agentSettings.state.stopped'),
    pending: t('admin.agentSettings.state.pending'), crashed: t('admin.agentSettings.state.crashed'),
    failed: t('admin.agentSettings.state.failed'), idle: t('admin.agentSettings.state.idle'),
  }
  const childPath = (name: string) => workspace.folder ? `${workspace.folder}/${name}` : name

  async function run(action: () => Promise<string>) {
    if (busy) return
    setBusy(true); setActionError(null)
    try {
      const status = await action()
      if (status !== 'ready') setActionError(statusMessage(status))
      await workspace.openFolder(workspace.folder)
    } catch {
      setActionError(t('workspace.managed.writeFailed'))
    } finally {
      setBusy(false)
    }
  }

  async function download(path: string) {
    if (!agentId || busy) return
    setActionError(null)
    setBusy(true)
    try {
      await downloadWorkspaceFile(agentId, path, { limits, onProgress: trackProgress(path.split('/').pop() ?? path) })
    } catch (cause) {
      const reason = cause instanceof Error ? cause.message : 'failed'
      setActionError(reason === 'too_large'
        ? (limits ? t('workspace.managed.tooLargeLimit', { limit: formatBytes(limits.transferMax) }) : t('workspace.managed.downloadTooLarge'))
        : t('workspace.managed.downloadFailed'))
    } finally {
      setBusy(false); setProgress(null)
    }
  }

  async function downloadZip(path: string) {
    if (!agentId || !limits || busy) return
    setActionError(null)
    setBusy(true)
    const name = path ? path.split('/').pop()! : t('agentSetup.managedWorkspace')
    try {
      await downloadWorkspaceArchive(agentId, path, 'workspace', trackProgress(`${name}.zip`))
    } catch (cause) {
      setActionError(cause instanceof Error && cause.message === 'too_large'
        ? t('workspace.managed.archiveTooLarge', { limit: formatBytes(limits.archiveMax) })
        : t('workspace.managed.downloadFailed'))
    } finally {
      setBusy(false); setProgress(null)
    }
  }

  async function remove(entry: WorkspaceEntry) {
    if (!agentId || !token) return
    const ok = await confirm({
      title: t('workspace.managed.deleteTitle', { name: entry.name }),
      description: entry.kind === 'directory' ? t('workspace.managed.deleteFolder') : t('workspace.managed.deleteFile'),
      confirmLabel: t('workspace.managed.delete'),
      destructive: true,
    })
    if (!ok) return
    await run(() => changeWorkspace(agentId, 'delete', { path: childPath(entry.name), edit_token: token }))
  }

  async function rename() {
    if (!agentId || !token || !renaming || !validEntryName(renaming.value) || renaming.value === renaming.name) return
    const { name, value } = renaming
    setRenaming(null)
    await run(() => changeWorkspace(agentId, 'rename', { path: childPath(name), new_name: value, edit_token: token }))
  }

  async function upload(files: File[]) {
    if (!agentId || !token || files.length === 0 || busy) return
    setBusy(true); setActionError(null); setUploads([])
    try {
      const outcomes = await uploadWorkspaceFiles(agentId, workspace.folder, files, token, {
        limits,
        onProgress: (name, loaded, total) => setProgress({ name, loaded, total }),
        canReplace: canManage,
        confirmReplace: name => confirm({
          title: t('workspace.managed.replaceTitle', { name }),
          description: t('workspace.managed.replaceDescription'),
          confirmLabel: t('workspace.managed.replace'),
          destructive: true,
        }),
      })
      setUploads(outcomes)
      await workspace.openFolder(workspace.folder)
    } finally {
      setBusy(false); setProgress(null)
    }
  }

  const dropEnabled = canEdit && workspace.filePath === null
  const dragProps = dropEnabled ? {
    onDragEnter: (event: DragEvent) => {
      if (!event.dataTransfer.types.includes('Files')) return
      event.preventDefault(); dragDepth.current += 1; setDragging(true)
    },
    onDragOver: (event: DragEvent) => {
      if (!event.dataTransfer.types.includes('Files')) return
      event.preventDefault(); event.dataTransfer.dropEffect = 'copy'
    },
    onDragLeave: () => {
      dragDepth.current = Math.max(0, dragDepth.current - 1)
      if (dragDepth.current === 0) setDragging(false)
    },
    onDrop: (event: DragEvent) => {
      event.preventDefault(); dragDepth.current = 0; setDragging(false)
      void upload(Array.from(event.dataTransfer.files))
    },
  } : {}

  const outcomeLabel: Record<UploadOutcome['status'], string> = {
    uploaded: t('workspace.managed.uploaded'), skipped: t('workspace.managed.skipped'), failed: t('workspace.managed.uploadFailed'),
  }

  return <section className="space-y-3" data-testid="managed-workspace-panel" aria-busy={workspace.loading || busy}>
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="flex items-center gap-2 text-sm font-medium"><FolderOpen className="h-4 w-4" />{t('agentSetup.managedWorkspace')}</h3>
      <Button variant="ghost" size="sm" disabled={!agentId || workspace.loading} onClick={() => void workspace.refresh()}><RefreshCw className={workspace.loading ? 'animate-spin' : ''} />{t('common.refresh')}</Button>
    </div>
    <p className="text-sm leading-relaxed text-[var(--color-foreground-muted)]">{t('workspace.managed.hint')}</p>
    {result?.machine_name && <p className="break-words text-sm">{t('workspace.managed.machine')}: {result.machine_name}</p>}
    {snapshot?.cwd && <dl className="grid gap-2 text-xs sm:grid-cols-[auto_1fr]">
      <dt className="text-[var(--color-foreground-muted)]">{t('workspace.managed.path')}</dt><dd className="min-w-0 break-all font-mono">{snapshot.cwd}</dd>
      <dt className="text-[var(--color-foreground-muted)]">{t('workspace.managed.runtime')}</dt><dd>{snapshot.engine} · {agentStates[result?.agent_state ?? ''] ?? t('admin.agentSettings.state.unknown')}{!snapshot.live && ` · ${t('workspace.managed.lastRun')}`}</dd>
      <dt className="text-[var(--color-foreground-muted)]">{t('workspace.managed.permission')}</dt><dd>{permission[snapshot.permission_level ?? ''] ?? snapshot.permission_level}</dd>
      {snapshot.reported_at && <><dt className="text-[var(--color-foreground-muted)]">{t('workspace.managed.reported')}</dt><dd>{formatDate(new Date(snapshot.reported_at), { dateStyle: 'short', timeStyle: 'medium' })}</dd></>}
    </dl>}
    {workspace.loading && <p role="status" className="flex items-center gap-2 text-sm text-[var(--color-foreground-muted)]"><Loader2 className="h-4 w-4 animate-spin" />{t('workspace.managed.loading')}</p>}
    {workspace.failed && <div role="alert" className="flex flex-wrap items-center gap-2 text-sm text-[var(--color-destructive)]"><p>{t('workspace.managed.failed')}</p><Button variant="outline" size="sm" onClick={() => void workspace.retry()}>{t('common.retry')}</Button></div>}
    {!workspace.loading && result && result.status !== 'ready' && <p role="status" className="text-sm text-[var(--color-foreground-muted)]">{statusMessage(result.status)}</p>}
    <div className="flex flex-wrap items-center gap-2">
      {(workspace.folder || workspace.filePath) && <Button variant="outline" size="sm" onClick={() => void workspace.openFolder(workspace.filePath ? workspace.folder : workspace.folder.split('/').slice(0, -1).join('/'))}><ArrowUp />{t('workspace.managed.back')}</Button>}
      {folder?.status === 'ready' && <p className="min-w-0 break-all font-mono text-xs text-[var(--color-foreground-muted)]">/{workspace.folder}</p>}
      {limits && workspace.filePath === null && folder?.status === 'ready' && <Button variant="outline" size="sm" className="ml-auto" disabled={busy} onClick={() => void downloadZip(workspace.folder)} data-testid="workspace-download-folder-zip"><FileArchive />{t('workspace.managed.downloadFolderZip')}</Button>}
    </div>
    {folder?.status === 'ready' && folder.can_manage_files && !folder.can_transfer && <p className="text-xs text-[var(--color-foreground-muted)]" data-testid="workspace-transfer-unavailable">{t('workspace.managed.transferUnavailable', { limit: '20 MiB' })}</p>}
    {progress && <div role="status" className="space-y-1 text-sm" data-testid="workspace-transfer-progress">
      <p className="break-all">{t('workspace.managed.transferring', { name: progress.name })}</p>
      <div className="h-2 overflow-hidden rounded-full bg-[var(--color-surface-alt)]" role="progressbar" aria-label={progress.name} aria-valuemin={0} aria-valuemax={progress.total ?? undefined} aria-valuenow={progress.loaded}>
        <div className="h-full bg-[var(--color-brand)] motion-safe:transition-[width]" style={{ width: progress.total ? `${Math.min(100, (progress.loaded / progress.total) * 100)}%` : '100%' }} />
      </div>
      <p className="text-caption text-[var(--color-foreground-muted)]">{progress.total ? t('workspace.managed.progress', { loaded: formatBytes(progress.loaded), total: formatBytes(progress.total) }) : formatBytes(progress.loaded)}</p>
    </div>}
    {agentId && workspace.filePath === null && folder?.status === 'ready' && folder.can_edit && folder.snapshot?.edit_token && <ManagedWorkspaceEditor
      key={`${agentId}:${workspace.folder}:directory`}
      agentId={agentId} folder={workspace.folder} filePath={null}
      folderData={folder} fileData={file} reloadFolder={() => workspace.openFolder(workspace.folder)}
      reloadFile={path => workspace.openFile(path)}
      onUploadFiles={files => void upload(files)}
    />}
    {folder?.status === 'ready' && !folder.can_edit && <p className="text-xs text-[var(--color-foreground-muted)]">{t('workspace.managed.editUnavailable')}</p>}
    {folder?.status === 'ready' && folder.can_edit && !folder.can_manage_files && <p className="text-xs text-[var(--color-foreground-muted)]" data-testid="workspace-manage-unavailable">{t('workspace.managed.manageUnavailable')}</p>}
    {workspace.filePath === null && folder?.status === 'ready' && folder.snapshot && <div
      className={`relative overflow-hidden rounded-[var(--radius-md)] border ${dragging ? 'border-[var(--color-brand)]' : 'border-[var(--color-border)]'}`}
      data-testid="workspace-file-list"
      {...dragProps}
    >
      {dragging && <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center gap-2 bg-[var(--color-surface-selected)] p-4 text-center text-sm font-medium text-[var(--color-brand-text)]" data-testid="workspace-drop-overlay">
        <Upload className="h-4 w-4 shrink-0" aria-hidden="true" />{t('workspace.managed.dropHere', { folder: workspace.folder })}
      </div>}
      {folder.snapshot.entries.length === 0 && <p className="p-3 text-sm text-[var(--color-foreground-muted)]">{t('workspace.managed.empty')}{dropEnabled && ` ${t('workspace.managed.dropHint')}`}</p>}
      <ul className="max-h-72 overflow-y-auto divide-y divide-[var(--color-border)]">
        {folder.snapshot.entries.map(entry => <li key={entry.name} className="flex items-center gap-1 pr-1">
          {renaming?.name === entry.name ? (
            <form className="flex min-w-0 flex-1 flex-wrap items-center gap-2 px-3 py-1.5" onSubmit={event => { event.preventDefault(); void rename() }}>
              <Input
                autoFocus
                aria-label={t('workspace.managed.newName')}
                value={renaming.value}
                maxLength={255}
                onChange={event => setRenaming({ name: entry.name, value: event.target.value })}
                onKeyDown={event => { if (event.key === 'Escape') setRenaming(null) }}
                className="min-w-0 flex-1"
                data-testid="workspace-rename-input"
              />
              <Button type="submit" size="sm" disabled={busy || !validEntryName(renaming.value) || renaming.value === entry.name}>{t('common.save')}</Button>
              <Button type="button" variant="outline" size="sm" onClick={() => setRenaming(null)}>{t('common.cancel')}</Button>
            </form>
          ) : <>
            <button type="button" className="flex min-h-[var(--control-height)] min-w-0 flex-1 items-center gap-2 px-3 py-2 text-left text-sm hover:bg-[var(--color-surface-alt)] disabled:cursor-default disabled:text-[var(--color-foreground-muted)]" disabled={entry.kind === 'link' || entry.kind === 'unavailable'} onClick={() => { if (entry.kind === 'directory') void workspace.openFolder(childPath(entry.name)); else void workspace.openFile(childPath(entry.name)) }}>
              {entry.kind === 'directory' ? <Folder className="h-4 w-4 shrink-0" /> : entry.kind === 'link' ? <Link2 className="h-4 w-4 shrink-0" /> : <FileText className="h-4 w-4 shrink-0" />}
              <span className="min-w-0 break-all">{entry.name}</span>
              {(entry.kind === 'link' || entry.kind === 'unavailable') && <span className="ml-auto shrink-0 text-xs">{t('workspace.managed.unavailableEntry')}</span>}
            </button>
            {canManage && (entry.kind === 'file' || entry.kind === 'directory') && <div className="flex shrink-0 items-center">
              {entry.kind === 'file' && <Button variant="ghost" size="icon" disabled={busy} aria-label={t('workspace.managed.downloadName', { name: entry.name })} title={t('workspace.managed.download')} onClick={() => void download(childPath(entry.name))} data-testid={`workspace-download-${entry.name}`}><Download /></Button>}
              {entry.kind === 'directory' && limits && <Button variant="ghost" size="icon" disabled={busy} aria-label={t('workspace.managed.downloadZipName', { name: entry.name })} title={t('workspace.managed.downloadZip')} onClick={() => void downloadZip(childPath(entry.name))} data-testid={`workspace-zip-${entry.name}`}><FileArchive /></Button>}
              <Button variant="ghost" size="icon" disabled={busy} aria-label={t('workspace.managed.renameName', { name: entry.name })} title={t('workspace.managed.rename')} onClick={() => setRenaming({ name: entry.name, value: entry.name })} data-testid={`workspace-rename-${entry.name}`}><Pencil /></Button>
              <Button variant="ghost" size="icon" disabled={busy} aria-label={t('workspace.managed.deleteName', { name: entry.name })} title={t('workspace.managed.delete')} onClick={() => void remove(entry)} data-testid={`workspace-delete-${entry.name}`}><Trash2 /></Button>
            </div>}
          </>}
        </li>)}
      </ul>
      {folder.snapshot.next_cursor && <div className="border-t border-[var(--color-border)] p-2"><Button variant="outline" size="sm" disabled={workspace.loading} onClick={() => void workspace.loadMore()}>{t('workspace.managed.loadMore')}</Button></div>}
    </div>}
    {uploads.length > 0 && <div role="status" className="space-y-1 rounded-[var(--radius-md)] border border-[var(--color-border)] p-3 text-sm" data-testid="workspace-upload-results">
      <p className="font-medium">{t('workspace.managed.uploadResults')}</p>
      <ul className="space-y-0.5">
        {uploads.map((outcome, index) => <li key={`${outcome.name}-${index}`} className="flex flex-wrap gap-x-2">
          <span className="min-w-0 break-all">{outcome.name}</span>
          <span className={outcome.status === 'failed' ? 'text-[var(--color-danger)]' : 'text-[var(--color-foreground-muted)]'}>
            {outcomeLabel[outcome.status]}{outcome.reason ? ` — ${statusMessage(outcome.reason)}` : ''}
          </span>
        </li>)}
      </ul>
    </div>}
    {actionError && <p role="alert" className="text-sm text-[var(--color-destructive)]">{actionError}</p>}
    {workspace.filePath !== null && <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="min-w-0 break-all font-mono text-xs">{workspace.filePath}</p>
        {file?.can_manage_files && file.status === 'ready' && <Button variant="outline" size="sm" onClick={() => void download(workspace.filePath!)} data-testid="workspace-download-current"><Download />{t('workspace.managed.download')}</Button>}
      </div>
      {agentId && folder?.status === 'ready' && folder.can_edit && folder.snapshot?.edit_token && <ManagedWorkspaceEditor
        key={`${agentId}:${workspace.filePath}`}
        agentId={agentId} folder={workspace.folder} filePath={workspace.filePath}
        folderData={folder} fileData={file} reloadFolder={() => workspace.openFolder(workspace.folder)}
        reloadFile={path => workspace.openFile(path)}
      />}
      {file?.status === 'ready' && file.snapshot?.preview_status === 'text' && (!folder?.can_edit || !file.snapshot.sha256) && <pre className="max-h-80 overflow-auto rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-surface-alt)] p-3 text-xs leading-relaxed" aria-label={t('workspace.managed.preview')}><code>{file.snapshot.text}</code></pre>}
      {imageUrl && <img src={imageUrl} alt={t('workspace.managed.imagePreview', { name: workspace.filePath.split('/').pop() ?? '' })} className="max-h-80 max-w-full rounded-[var(--radius-md)] border border-[var(--color-border)] object-contain" data-testid="workspace-image-preview" />}
      {!imageUrl && file?.status === 'ready' && file.snapshot?.preview_status === 'binary' && <p className="text-sm text-[var(--color-foreground-muted)]">{t('workspace.managed.binary')}</p>}
      {!imageUrl && file?.status === 'ready' && file.snapshot?.preview_status === 'too_large' && <p className="text-sm text-[var(--color-foreground-muted)]">{t('workspace.managed.tooLarge')}</p>}
    </div>}
  </section>
}

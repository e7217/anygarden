import { ArrowUp, FileText, Folder, FolderOpen, Link2, Loader2, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useManagedWorkspace } from '@/hooks/useManagedWorkspace'
import { useLocale } from '@/i18n/LocaleProvider'

export default function ManagedWorkspacePanel({ agentId }: { agentId: string | null }) {
  const { t, formatDate } = useLocale()
  const workspace = useManagedWorkspace(agentId)
  const folder = workspace.folderData
  const file = workspace.fileData
  const result = file ?? folder
  const snapshot = result?.snapshot
  const statusMessage = (status: string) => {
    const messages: Record<string, string> = {
      not_placed: t('workspace.managed.notPlaced'), offline: t('workspace.managed.offline'),
      unsupported: t('workspace.managed.unsupported'), not_ready: t('workspace.managed.notReady'),
      stale: t('workspace.managed.stale'), blocked: t('workspace.managed.blocked'),
      not_found: t('workspace.managed.notFound'), too_many_entries: t('workspace.managed.tooMany'),
      timeout: t('workspace.managed.timeout'), busy: t('workspace.managed.busy'),
    }
    return messages[status] ?? t('workspace.managed.failed')
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
  return <section className="space-y-3" data-testid="managed-workspace-panel" aria-busy={workspace.loading}>
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="flex items-center gap-2 text-sm font-medium"><FolderOpen className="h-4 w-4" />{t('agentSetup.managedWorkspace')}</h3>
      <Button variant="ghost" size="sm" disabled={!agentId || workspace.loading} onClick={() => void workspace.refresh()}><RefreshCw className={workspace.loading ? 'animate-spin' : ''} />{t('common.refresh')}</Button>
    </div>
    <p className="text-xs leading-relaxed text-[var(--color-foreground-muted)]">{t('workspace.managed.hint')}</p>
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
    </div>
    {workspace.filePath === null && folder?.status === 'ready' && folder.snapshot && <div className="overflow-hidden rounded-[var(--radius-md)] border border-[var(--color-border)]">
      {folder.snapshot.entries.length === 0 && <p className="p-3 text-sm text-[var(--color-foreground-muted)]">{t('workspace.managed.empty')}</p>}
      <ul className="max-h-72 overflow-y-auto divide-y divide-[var(--color-border)]">
        {folder.snapshot.entries.map(entry => <li key={entry.name}>
          <button type="button" className="flex min-h-[var(--control-height)] w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-[var(--color-surface-alt)] disabled:cursor-default disabled:text-[var(--color-foreground-muted)]" disabled={entry.kind === 'link' || entry.kind === 'unavailable'} onClick={() => { if (entry.kind === 'directory') void workspace.openFolder(childPath(entry.name)); else void workspace.openFile(childPath(entry.name)) }}>
            {entry.kind === 'directory' ? <Folder className="h-4 w-4 shrink-0" /> : entry.kind === 'link' ? <Link2 className="h-4 w-4 shrink-0" /> : <FileText className="h-4 w-4 shrink-0" />}
            <span className="min-w-0 break-all">{entry.name}</span>
            {(entry.kind === 'link' || entry.kind === 'unavailable') && <span className="ml-auto shrink-0 text-xs">{t('workspace.managed.unavailableEntry')}</span>}
          </button>
        </li>)}
      </ul>
      {folder.snapshot.next_cursor && <div className="border-t border-[var(--color-border)] p-2"><Button variant="outline" size="sm" disabled={workspace.loading} onClick={() => void workspace.loadMore()}>{t('workspace.managed.loadMore')}</Button></div>}
    </div>}
    {workspace.filePath !== null && <div className="space-y-2">
      <p className="break-all font-mono text-xs">{workspace.filePath}</p>
      {file?.status === 'ready' && file.snapshot?.preview_status === 'text' && <pre className="max-h-80 overflow-auto rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-surface-alt)] p-3 text-xs leading-relaxed" aria-label={t('workspace.managed.preview')}><code>{file.snapshot.text}</code></pre>}
      {file?.status === 'ready' && file.snapshot?.preview_status === 'binary' && <p className="text-sm text-[var(--color-foreground-muted)]">{t('workspace.managed.binary')}</p>}
      {file?.status === 'ready' && file.snapshot?.preview_status === 'too_large' && <p className="text-sm text-[var(--color-foreground-muted)]">{t('workspace.managed.tooLarge')}</p>}
    </div>}
  </section>
}

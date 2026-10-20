/**
 * MachineChangeDialog — #907. Moves an agent to another machine.
 *
 * The server never moves a placed agent on its own, so this dialog is the
 * only way an agent's machine changes. Before the move it states what does
 * not follow the agent: workspace files and memory stay on the old disk,
 * engine sign-in and host access come from the new machine.
 */
import { useEffect, useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { apiFetch } from '@/lib/api'
import { useLocale } from '@/i18n/LocaleProvider'

/** #915 — whether the work files can follow the agent to a machine. */
export interface MovePreview {
  can_move: boolean
  reason: string | null
  files: number
  bytes: number
  limit_bytes: number
}

export interface MachineChoice {
  id: string
  name: string
  status: string
}

async function defaultListMachines(): Promise<MachineChoice[]> {
  const resp = await apiFetch('/api/v1/machines')
  if (!resp.ok) throw new Error('machines')
  return await resp.json() as MachineChoice[]
}

async function defaultListMachineEngines(machineId: string): Promise<string[]> {
  const resp = await apiFetch(`/api/v1/machines/${machineId}/engines`)
  if (!resp.ok) throw new Error('engines')
  const rows = await resp.json() as Array<{ engine: string }>
  return rows.map(row => row.engine)
}

type ListState = { kind: 'loading' } | { kind: 'error' } | { kind: 'ready'; machines: MachineChoice[] }

function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KiB`
  return `${bytes} B`
}

const KNOWN_REASONS = new Set(['source_offline', 'source_unsupported', 'target_unsupported', 'too_large', 'nothing'])

export default function MachineChangeDialog({
  open, onOpenChange, agentId, agentName, engine, trusted, currentMachineId, currentMachineName, onSubmit,
  listMachines = defaultListMachines, listMachineEngines = defaultListMachineEngines, previewMove,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  agentId: string
  agentName: string
  engine: string
  trusted: boolean
  currentMachineId: string | null
  currentMachineName: string | null
  onSubmit: (machineId: string, options: { moveFiles: boolean }) => Promise<void>
  listMachines?: () => Promise<MachineChoice[]>
  listMachineEngines?: (machineId: string) => Promise<string[]>
  previewMove?: (machineId: string) => Promise<MovePreview>
}) {
  const { t } = useLocale()
  const [list, setList] = useState<ListState>({ kind: 'loading' })
  const [machineId, setMachineId] = useState('')
  const [supported, setSupported] = useState<boolean | null>(null)
  const [preview, setPreview] = useState<MovePreview | null>(null)
  const [moveFiles, setMoveFiles] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!open) return
    let active = true
    setList({ kind: 'loading' }); setError('')
    listMachines()
      .then(rows => {
        if (!active) return
        const choices = rows.filter(row => row.status === 'online' && row.id !== currentMachineId)
        setList({ kind: 'ready', machines: choices })
        setMachineId(choices[0]?.id ?? '')
      })
      .catch(() => { if (active) setList({ kind: 'error' }) })
    return () => { active = false }
  }, [open, currentMachineId, listMachines])

  useEffect(() => {
    setSupported(null)
    if (!machineId) return
    let active = true
    listMachineEngines(machineId)
      .then(engines => { if (active) setSupported(engines.includes(engine)) })
      // An unknown answer must not block the move; the server re-checks it.
      .catch(() => { if (active) setSupported(true) })
    return () => { active = false }
  }, [machineId, engine, listMachineEngines])

  useEffect(() => {
    setPreview(null); setMoveFiles(false)
    if (!machineId) return
    let active = true
    const load = previewMove ?? (async (id: string) => {
      const resp = await apiFetch(`/api/v1/agents/${encodeURIComponent(agentId)}/machine/move-preview?machine_id=${encodeURIComponent(id)}`)
      if (!resp.ok) throw new Error('preview')
      return await resp.json() as MovePreview
    })
    load(machineId)
      .then(result => { if (active) { setPreview(result); setMoveFiles(result.can_move) } })
      .catch(() => { if (active) setPreview({ can_move: false, reason: 'other', files: 0, bytes: 0, limit_bytes: 0 }) })
    return () => { active = false }
  }, [machineId, agentId, previewMove])

  const machines = list.kind === 'ready' ? list.machines : []
  const target = machines.find(machine => machine.id === machineId)
  const ready = !!target && supported === true

  const submit = async () => {
    setBusy(true); setError('')
    try {
      await onSubmit(machineId, { moveFiles: moveFiles && Boolean(preview?.can_move) })
      onOpenChange(false)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t('admin.machineChange.failed'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={next => { if (!busy) onOpenChange(next) }}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{t('admin.machineChange.title')}</DialogTitle>
          <DialogDescription>{t('admin.machineChange.description', { agent: agentName })}</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-1">
            <p className="text-caption text-[var(--color-foreground-muted)]">{t('admin.machineChange.current')}</p>
            <p className="text-sm text-[var(--color-foreground)]">{currentMachineName ?? t('admin.machineChange.none')}</p>
          </div>
          <div className="space-y-2">
            <Label htmlFor="machine-change-target">{t('admin.machineChange.target')}</Label>
            {list.kind === 'loading' && <p className="text-sm text-[var(--color-foreground-muted)]">{t('admin.machineChange.loading')}</p>}
            {list.kind === 'error' && <p role="alert" className="text-sm text-[var(--color-danger)]">{t('admin.machineChange.loadFailed')}</p>}
            {list.kind === 'ready' && machines.length === 0 && (
              <p className="text-sm text-[var(--color-foreground-muted)]" data-testid="machine-change-empty">{t('admin.machineChange.empty')}</p>
            )}
            {machines.length > 0 && (
              <Select id="machine-change-target" data-testid="machine-change-target" value={machineId} onChange={e => setMachineId(e.target.value)} disabled={busy}>
                {machines.map(machine => <option key={machine.id} value={machine.id}>{machine.name}</option>)}
              </Select>
            )}
            {target && supported === null && <p className="text-sm text-[var(--color-foreground-muted)]">{t('admin.machineChange.checking')}</p>}
            {target && supported === false && (
              <p role="alert" className="text-sm text-[var(--color-danger)]" data-testid="machine-change-engine-missing">
                {t('admin.machineChange.engineMissing', { machine: target.name, engine })}
              </p>
            )}
          </div>
          {target && (
            <div className="flex gap-2 rounded-[var(--radius-md)] border border-[var(--color-warning)] bg-[var(--color-warning-soft)] p-3 text-sm text-[var(--color-foreground)]" data-testid="machine-change-warning">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-warning)]" aria-hidden="true" />
              <div className="min-w-0 space-y-1">
                <p className="font-medium">{t('admin.machineChange.warningTitle')}</p>
                <ul className="list-disc space-y-1 pl-4">
                  <li>{moveFiles ? t('admin.machineChange.warningFilesMoved') : t('admin.machineChange.warningFiles')}</li>
                  <li>{t('admin.machineChange.warningLogin', { machine: target.name })}</li>
                  {trusted && <li>{t('admin.machineChange.warningTrusted', { machine: target.name })}</li>}
                </ul>
              </div>
            </div>
          )}
          {target && <div className="space-y-1" data-testid="machine-change-move-files">
            {preview === null && <p className="text-sm text-[var(--color-foreground-muted)]">{t('admin.machineChange.moveFilesChecking')}</p>}
            {preview?.can_move && <>
              <label className="flex min-h-[var(--control-height)] items-start gap-2 text-sm text-[var(--color-foreground)]">
                <input
                  type="checkbox"
                  className="mt-0.5 h-4 w-4 accent-[var(--color-brand)]"
                  checked={moveFiles}
                  disabled={busy}
                  onChange={event => setMoveFiles(event.target.checked)}
                  data-testid="machine-change-move-files-checkbox"
                />
                <span>{t('admin.machineChange.moveFiles', { files: preview.files, size: formatBytes(preview.bytes) })}</span>
              </label>
              <p className="pl-6 text-caption text-[var(--color-foreground-muted)]">{t('admin.machineChange.moveFilesHint')}</p>
            </>}
            {preview && !preview.can_move && <p className="text-sm text-[var(--color-foreground-muted)]" data-testid="machine-change-move-files-reason">
              {t(KNOWN_REASONS.has(preview.reason ?? '')
                ? `admin.machineChange.moveFilesUnavailable.${preview.reason}` as Parameters<typeof t>[0]
                : 'admin.machineChange.moveFilesUnavailable.other',
              { files: preview.files, size: formatBytes(preview.bytes), limit: formatBytes(preview.limit_bytes) })}
            </p>}
          </div>}
          <p className="text-sm text-[var(--color-foreground-muted)]">{t('admin.machineChange.instructionsKept')}</p>
          {busy && moveFiles && <p role="status" className="text-sm text-[var(--color-foreground)]" data-testid="machine-change-moving">{t('admin.machineChange.movingFiles')}</p>}
          {error && <p role="alert" className="text-sm text-[var(--color-danger)]">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>{t('common.cancel')}</Button>
          <Button onClick={() => void submit()} disabled={!ready || busy} data-testid="machine-change-submit">
            {busy ? t('admin.machineChange.saving') : t('admin.machineChange.submit')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

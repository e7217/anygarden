import { useState } from 'react'
import { Check, Copy, Loader2, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { useLocale } from '@/i18n/LocaleProvider'

/** Only accept a server base URL: credentials and request-specific parts have no place in a daemon command. */
export function machineServerUrl(value: string): URL | null {
  try {
    const url = new URL(value.trim())
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) return null
    return url
  } catch {
    return null
  }
}

function shellQuote(value: string): string {
  return `'${value.replace(/'/g, `'"'"'`)}'`
}

export function machineRunCommand(server: URL, machineId: string, needsToken: boolean): string {
  const options = `--server ${shellQuote(server.toString().replace(/\/$/, ''))} \\\n  --machine-id ${shellQuote(machineId)}`
  if (!needsToken) return `anygarden-machine run ${options}`
  // connect prompts privately and saves the existing identity, without registering a duplicate.
  return `anygarden-machine connect ${options} &&\nanygarden-machine run`
}

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  machine: { id: string; name: string; status?: string }
  token?: string
  refreshWarning?: string
  onCheck: () => Promise<void>
}

export default function MachineConnectionDialog({ open, onOpenChange, machine, token, refreshWarning, onCheck }: Props) {
  const { t } = useLocale()
  const [server, setServer] = useState(() => window.location.origin)
  const [checking, setChecking] = useState(false)
  const [enterToken, setEnterToken] = useState(Boolean(token))
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState<string | null>(null)
  const serverUrl = machineServerUrl(server)
  const loopback = serverUrl && ['localhost', '127.0.0.1', '[::1]', '0.0.0.0'].includes(serverUrl.hostname)
  const command = serverUrl ? machineRunCommand(serverUrl, machine.id, enterToken) : ''
  const online = machine.status === 'online'

  async function copy(value: string, name: string) {
    setError(null)
    try {
      await navigator.clipboard.writeText(value)
      setCopied(name)
    } catch {
      setError(t('admin.machines.copyFailed'))
    }
  }

  function commandBlock(value: string, name: string) {
    return (
      <div className="overflow-hidden rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-surface-alt)]">
        <div className="flex items-center justify-between gap-2 border-b border-[var(--color-border)] px-3 py-1">
          <span className="text-xs font-medium text-[var(--color-foreground-muted)]">{name}</span>
          <Button variant="ghost" size="sm" onClick={() => void copy(value, name)} aria-label={t('admin.machines.copyCommand', { name })}>
            {copied === name ? <Check /> : <Copy />}
            {copied === name ? t('common.copied') : t('common.copy')}
          </Button>
        </div>
        <pre className="overflow-x-auto p-3 text-xs leading-relaxed"><code>{value}</code></pre>
      </div>
    )
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t('admin.machines.connectionTitle', { name: machine.name })}</DialogTitle>
          <DialogDescription>{t('admin.machines.connectionDescription')}</DialogDescription>
        </DialogHeader>
        <div className="space-y-5 text-sm">
          {refreshWarning && <p role="alert" className="rounded-[var(--radius-md)] border border-[var(--color-warning)] p-3 text-[var(--color-warning)]">{refreshWarning}</p>}
          <section className="space-y-2">
            <h3 className="font-semibold">{t('admin.machines.installStep')}</h3>
            <p className="text-[var(--color-foreground-muted)]">{t('admin.machines.installHint')}</p>
            {commandBlock('python3 -m venv ~/.anygarden/machine-venv\nsource ~/.anygarden/machine-venv/bin/activate\npython -m pip install --upgrade anygarden-machine', `${t('admin.machines.installCommand')} (Bash)`)}
          </section>
          <section className="space-y-3">
            <h3 className="font-semibold">{t('admin.machines.runStep')}</h3>
            <div className="space-y-2">
              <Label htmlFor="machine-server-url">{t('admin.machines.serverUrl')}</Label>
              <Input id="machine-server-url" type="url" value={server} onChange={event => { setServer(event.target.value); setCopied(null) }} aria-describedby="machine-server-hint" aria-invalid={!serverUrl} autoComplete="url" />
              <p id="machine-server-hint" className="text-xs text-[var(--color-foreground-muted)]">{t('admin.machines.serverHint')}</p>
              {loopback && <p className="text-xs text-[var(--color-warning)]">{t('admin.machines.localhostHint')}</p>}
              {!serverUrl && <p role="alert" className="text-xs text-[var(--color-destructive)]">{t('admin.machines.invalidServerUrl')}</p>}
            </div>
            {token ? (
              <div className="space-y-2 rounded-[var(--radius-md)] border border-[var(--color-border)] p-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{t('admin.machines.machineToken')}</span>
                  <Button variant="ghost" size="sm" aria-label={t('admin.machines.copyCommand', { name: t('admin.machines.machineToken') })} onClick={() => void copy(token, 'token')}>
                    {copied === 'token' ? <Check /> : <Copy />}
                    {copied === 'token' ? t('common.copied') : t('common.copy')}
                  </Button>
                </div>
                <code className="block select-all break-all text-xs">{token}</code>
                <p className="text-xs text-[var(--color-warning)]">{t('admin.machines.copyTokenWarning')}</p>
                <p className="text-xs text-[var(--color-foreground-muted)]">{t('admin.machines.tokenPromptHint')}</p>
              </div>
            ) : <div className="space-y-2">
              <p className="text-xs text-[var(--color-foreground-muted)]">{t('admin.machines.savedTokenHint')}</p>
              <label className="flex min-h-11 cursor-pointer items-center gap-2 text-sm md:min-h-8">
                <input type="checkbox" checked={enterToken} onChange={event => { setEnterToken(event.target.checked); setCopied(null) }} className="size-4 accent-[var(--color-brand)]" />
                {t('admin.machines.enterToken')}
              </label>
            </div>}
            {command && commandBlock(command, `${t('admin.machines.runCommand')} (Bash)`)}
            <p className="text-xs text-[var(--color-foreground-muted)]">{t('admin.machines.foregroundHint')}</p>
            <details className="space-y-2 text-xs text-[var(--color-foreground-muted)]">
              <summary className="cursor-pointer py-2 font-medium">{t('admin.machines.restartLater')}</summary>
              <p>{t('admin.machines.restartHint')}</p>
              {commandBlock('source ~/.anygarden/machine-venv/bin/activate\nanygarden-machine run', t('admin.machines.restartCommand'))}
              <p>{t('admin.machines.serviceHint')}</p>
              {commandBlock('anygarden-machine install-systemd-unit\nsystemctl --user daemon-reload\nsystemctl --user enable --now anygarden-machine', t('admin.machines.serviceCommand'))}
            </details>
          </section>
          <section className="space-y-2">
            <h3 className="font-semibold">{t('admin.machines.verifyStep')}</h3>
            <p role="status" className={online ? 'text-[var(--color-success)]' : 'text-[var(--color-foreground-muted)]'}>
              {online ? t('admin.machines.connectionReady') : t('admin.machines.waitingConnection')}
            </p>
            <Button variant="outline" size="sm" disabled={checking} onClick={async () => {
              setChecking(true)
              setError(null)
              try { await onCheck() } catch { setError(t('admin.machines.connectionCheckFailed')) }
              finally { setChecking(false) }
            }}>
              {checking ? <Loader2 className="animate-spin" /> : <RefreshCw />}
              {checking ? t('admin.machines.checkingConnection') : t('admin.machines.checkConnection')}
            </Button>
            {!online && <details className="pt-1 text-xs text-[var(--color-foreground-muted)]">
              <summary className="cursor-pointer py-2 font-medium">{t('admin.machines.troubleshoot')}</summary>
              <p className="leading-relaxed">{t('admin.machines.troubleshootHint')}</p>
            </details>}
          </section>
          {error && <p role="alert" className="text-sm text-[var(--color-destructive)]">{error}</p>}
        </div>
        <DialogFooter><Button onClick={() => onOpenChange(false)}>{t('admin.machines.done')}</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

import { useEffect, useRef, useState } from 'react'
import { Check, Copy, RefreshCw } from 'lucide-react'
import { apiFetch } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { useLocale } from '@/i18n/LocaleProvider'
import type { MessageKey } from '@/i18n/messages'

// Commands are shell text, not copy: keep them out of the catalogs.
const ENGINES: Array<{ name: string; label: MessageKey; command: string }> = [
  { name: 'Codex', label: 'agentSetup.engineSetup.codex', command: 'npm i -g @openai/codex && codex login' },
  { name: 'Pi', label: 'agentSetup.engineSetup.pi', command: 'npm i -g @earendil-works/pi-coding-agent' },
]
const POLL_MS = 3000
// A first managed Pi install runs npm on the machine (#688); give it ~90s.
const POLL_ATTEMPTS = 30

/**
 * What to do when a machine reports no engine (#902): the install commands,
 * and "Detect again", which asks the daemon to re-detect without a restart
 * and polls the machine's engines until one shows up.
 */
export default function EngineSetupHint({ machineId, machineName, onDetected }: {
  machineId: string
  machineName?: string | null
  onDetected: () => void
}) {
  const { t } = useLocale()
  const [detecting, setDetecting] = useState(false)
  const [message, setMessage] = useState('')
  const [copied, setCopied] = useState<string | null>(null)
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])
  const machine = machineName || t('agentSetup.codexLogin.thisMachine')

  async function detect() {
    setDetecting(true); setMessage('')
    try {
      const response = await apiFetch(`/api/v1/machines/${machineId}/engines/rescan`, { method: 'POST' })
      if (!response.ok) throw new Error('rescan failed')
      for (let attempt = 0; attempt < POLL_ATTEMPTS && mounted.current; attempt++) {
        await new Promise(resolve => setTimeout(resolve, POLL_MS))
        const engines = await apiFetch(`/api/v1/machines/${machineId}/engines`)
        if (engines.ok && ((await engines.json()) as unknown[]).length > 0) {
          if (mounted.current) onDetected()
          return
        }
      }
      if (mounted.current) setMessage(t('agentSetup.engineSetup.pending'))
    } catch {
      if (mounted.current) setMessage(t('agentSetup.engineSetup.failed'))
    } finally {
      if (mounted.current) setDetecting(false)
    }
  }

  async function copy(name: string, command: string) {
    try {
      await navigator.clipboard.writeText(command)
      setCopied(name)
    } catch {
      setMessage(t('admin.machines.copyFailed'))
    }
  }

  return (
    <div data-testid="engine-setup-hint" className="space-y-3 rounded-[var(--radius-md)] bg-[var(--color-surface-alt)] p-3">
      <div className="space-y-1">
        <p className="text-sm font-medium text-[var(--color-foreground)]">{t('agentSetup.engineSetup.title', { machine })}</p>
        <p className="text-sm text-[var(--color-foreground-muted)]">{t('agentSetup.engineSetup.body', { machine })}</p>
      </div>
      <ul className="space-y-2">
        {ENGINES.map(engine => (
          <li key={engine.name} className="space-y-1">
            <div className="flex items-center justify-between gap-2">
              <span className="text-sm text-[var(--color-foreground)]">{t(engine.label)}</span>
              <Button variant="ghost" size="sm" onClick={() => void copy(engine.name, engine.command)}
                aria-label={t('agentSetup.engineSetup.copy', { name: engine.name })}>
                {copied === engine.name ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}
                {copied === engine.name ? t('common.copied') : t('common.copy')}
              </Button>
            </div>
            <code className="block overflow-x-auto rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1 text-sm">{engine.command}</code>
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="outline" size="sm" disabled={detecting} onClick={() => void detect()}>
          <RefreshCw className={`h-3.5 w-3.5 ${detecting ? 'motion-safe:animate-spin' : ''}`} aria-hidden="true" />
          {detecting ? t('agentSetup.engineSetup.detecting') : t('agentSetup.engineSetup.detect')}
        </Button>
        {message && <p role="status" className="text-sm text-[var(--color-foreground-muted)]">{message}</p>}
      </div>
    </div>
  )
}

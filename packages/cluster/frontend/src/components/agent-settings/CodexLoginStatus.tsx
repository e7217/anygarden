import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, CheckCircle2, HelpCircle, RefreshCw } from 'lucide-react'
import { apiFetch } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { useLocale } from '@/i18n/LocaleProvider'
import type { MessageKey } from '@/i18n/messages'

export type CodexAuthStatus = 'chatgpt' | 'api_key' | 'other' | 'none' | 'unknown'

interface EngineRow { engine: string; auth_status?: string | null; auth_checked_at?: string | null }

const COPY = {
  chatgpt: { title: 'agentSetup.codexLogin.chatgptTitle', body: 'agentSetup.codexLogin.chatgptBody' },
  api_key: { title: 'agentSetup.codexLogin.apiKeyTitle', body: 'agentSetup.codexLogin.apiKeyBody' },
  other: { title: 'agentSetup.codexLogin.otherTitle', body: 'agentSetup.codexLogin.otherBody' },
  none: { title: 'agentSetup.codexLogin.noneTitle', body: 'agentSetup.codexLogin.noneBody' },
  unknown: { title: 'agentSetup.codexLogin.unknownTitle', body: 'agentSetup.codexLogin.unknownBody' },
} as const satisfies Record<CodexAuthStatus, { title: MessageKey; body: MessageKey }>
const KNOWN = Object.keys(COPY) as CodexAuthStatus[]
const RECHECK_POLL_MS = 1500
const RECHECK_ATTEMPTS = 8

function normalize(value: string | null | undefined): CodexAuthStatus {
  return KNOWN.includes(value as CodexAuthStatus) ? value as CodexAuthStatus : 'unknown'
}

/**
 * Whether the placement machine's Codex is signed in (#715). Codex agents use
 * the machine user's `codex login`, so this is the only credential the
 * default Codex connection has. "Check again" asks the daemon to re-run
 * `codex login status` and polls until a newer report arrives.
 */
export default function CodexLoginStatus({ machineId, machineName }: { machineId: string; machineName?: string | null }) {
  const { t } = useLocale()
  const [row, setRow] = useState<EngineRow | null>(null)
  const [checking, setChecking] = useState(false)
  const [error, setError] = useState('')
  const mounted = useRef(true)

  const load = useCallback(async () => {
    const response = await apiFetch(`/api/v1/machines/${machineId}/engines`)
    if (!response.ok) throw new Error('load failed')
    const rows = await response.json() as EngineRow[]
    const next = rows.find(item => item.engine === 'codex-cli') ?? { engine: 'codex-cli' }
    if (mounted.current) setRow(next)
    return next
  }, [machineId])

  useEffect(() => {
    mounted.current = true
    load().catch(() => { if (mounted.current) setRow({ engine: 'codex-cli' }) })
    return () => { mounted.current = false }
  }, [load])

  async function recheck() {
    setChecking(true); setError('')
    const before = row?.auth_checked_at ?? null
    try {
      const response = await apiFetch(`/api/v1/machines/${machineId}/engines/codex-cli/check`, { method: 'POST' })
      if (!response.ok) throw new Error('check failed')
      for (let attempt = 0; attempt < RECHECK_ATTEMPTS && mounted.current; attempt++) {
        await new Promise(resolve => setTimeout(resolve, RECHECK_POLL_MS))
        const next = await load()
        if ((next.auth_checked_at ?? null) !== before) return
      }
      if (mounted.current) setError(t('agentSetup.codexLogin.recheckPending'))
    } catch {
      if (mounted.current) setError(t('agentSetup.codexLogin.recheckFailed'))
    } finally {
      if (mounted.current) setChecking(false)
    }
  }

  if (!row) return null
  const status = normalize(row.auth_status)
  const machine = machineName || t('agentSetup.codexLogin.thisMachine')
  const Icon = status === 'none' ? AlertTriangle : status === 'unknown' ? HelpCircle : CheckCircle2
  const tone = status === 'none'
    ? 'text-[var(--color-warning)]'
    : status === 'unknown' ? 'text-[var(--color-foreground-muted)]' : 'text-[var(--color-success)]'
  return (
    <div data-testid="codex-login-status" data-status={status} className="space-y-1 rounded-[var(--radius-md)] bg-[var(--color-surface-alt)] p-3">
      <div className="flex items-center gap-2">
        <Icon className={`h-4 w-4 shrink-0 ${tone}`} aria-hidden="true" />
        <span className="flex-1 text-sm font-medium">{t(COPY[status].title)}</span>
        <Button variant="ghost" size="sm" disabled={checking} onClick={() => void recheck()}>
          <RefreshCw className={`h-3.5 w-3.5 ${checking ? 'motion-safe:animate-spin' : ''}`} aria-hidden="true" />
          {checking ? t('agentSetup.codexLogin.checking') : t('agentSetup.codexLogin.recheck')}
        </Button>
      </div>
      <p className="text-sm text-[var(--color-foreground-muted)]">{t(COPY[status].body, { machine })}</p>
      {error && <p role="status" className="text-xs text-[var(--color-foreground-muted)]">{error}</p>}
    </div>
  )
}

import { useEffect, useState } from 'react'
import { apiFetch } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useLocale } from '@/i18n/LocaleProvider'

interface Status { configured: boolean; provider: string | null; revision: number | null }

export default function PiNativeAuthPanel({ agentId, provider, onSaved }: {
  agentId: string; provider: string | null; onSaved: () => Promise<unknown>
}) {
  const { t } = useLocale()
  const path = `/api/v1/agents/${agentId}/pi-auth`
  const [status, setStatus] = useState<Status | null>(null)
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  useEffect(() => {
    let active = true
    apiFetch(path).then(async response => {
      if (!response.ok) throw new Error(t('admin.piAuth.loadFailed'))
      const next = await response.json() as Status
      if (active) setStatus(next)
    }).catch(() => { if (active) setError(t('admin.piAuth.loadFailed')) })
    return () => { active = false }
  }, [path, t])

  async function update(method: 'PUT' | 'DELETE') {
    const body = method === 'PUT' ? JSON.stringify({ value }) : undefined
    setValue('')
    setError(''); setNotice(''); setBusy(true)
    try {
      const response = await apiFetch(path, { method, ...(body ? { body } : {}) })
      if (!response.ok) {
        const result = await response.json().catch(() => ({}))
        throw new Error(typeof result.detail === 'string' ? result.detail : t('admin.piAuth.updateFailed'))
      }
      setStatus(method === 'DELETE' ? { configured: false, provider: null, revision: null } : await response.json())
      setNotice(method === 'DELETE' ? t('admin.piAuth.removed') : t('admin.piAuth.saved'))
      await onSaved()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t('admin.piAuth.updateFailed'))
    } finally {
      setBusy(false)
    }
  }

  const active = status?.configured && status.provider === provider
  // Rendered inside the model connection form, so no card or heading of its own.
  return <section className="space-y-2" aria-label={t('admin.piAuth.title')}>
    <p className="text-sm text-[var(--color-foreground-muted)]">
      {active ? t('admin.piAuth.stored', { provider: provider ?? '', revision: status.revision ?? '' }) : status?.provider
        ? t('admin.piAuth.otherStored', { stored: status.provider, selected: provider ?? t('admin.piAuth.selectedProvider') })
        : t('admin.piAuth.noneStored')}
    </p>
    {error && <p role="alert" className="text-sm text-[var(--color-destructive)]">{error}</p>}
    {notice && <p role="status" className="text-sm">{notice}</p>}
    <fieldset disabled={busy || !provider} className="space-y-2">
      <label className="block space-y-1 text-sm font-medium">{t('admin.piAuth.providerKey')}
        <Input aria-label={t('admin.piAuth.keyLabel')} type="password" autoComplete="off" value={value} onChange={event => setValue(event.target.value)} />
      </label>
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" onClick={() => void update('PUT')} disabled={!value}>{t('admin.piAuth.saveKey')}</Button>
        {status?.provider && <Button variant="ghost" onClick={() => void update('DELETE')}>{t('admin.piAuth.removeKey')}</Button>}
      </div>
    </fieldset>
  </section>
}

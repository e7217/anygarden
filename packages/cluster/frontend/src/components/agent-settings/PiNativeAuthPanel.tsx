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
  return <section className="space-y-3 rounded border border-[var(--color-border)] p-3" aria-label={t('admin.piAuth.title')}>
    <h3 className="font-medium">{t('admin.piAuth.title')}</h3>
    <p className="text-sm text-[var(--color-foreground-muted)]">
      {active ? t('admin.piAuth.stored', { provider: provider ?? '', revision: status.revision ?? '' }) : status?.provider
        ? t('admin.piAuth.otherStored', { stored: status.provider, selected: provider ?? t('admin.piAuth.selectedProvider') })
        : t('admin.piAuth.noneStored')}
    </p>
    <p className="text-sm">{t('admin.piAuth.description')}</p>
    {error && <p role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    <fieldset disabled={busy || !provider} className="space-y-2">
      <label className="block">{t('admin.piAuth.providerKey')}
        <Input aria-label={t('admin.piAuth.keyLabel')} type="password" autoComplete="off" value={value} onChange={event => setValue(event.target.value)} />
      </label>
      <div className="flex gap-2">
        <Button onClick={() => void update('PUT')} disabled={!value}>{t('admin.piAuth.saveKey')}</Button>
        {status?.provider && <Button variant="outline" onClick={() => void update('DELETE')}>{t('admin.piAuth.removeKey')}</Button>}
      </div>
    </fieldset>
  </section>
}

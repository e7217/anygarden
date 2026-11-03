import { useCallback, useEffect, useState } from 'react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { useFeedback } from '@/components/feedback/FeedbackProvider'
import { apiFetch } from '@/lib/api'
import { useLocale } from '@/i18n/LocaleProvider'
import type { MessageKey } from '@/i18n/messages'

// Mirrors ``ApiKeyOut`` in packages/cluster/anygarden/auth/api_key_routes.py.
interface ApiKey {
  id: string
  name: string
  scope: ApiKeyScope
  prefix: string
  created_at: string
  expires_at: string | null
  last_used_at: string | null
}

type ApiKeyScope = 'read' | 'write' | 'admin'

const SCOPE_LABELS: Record<ApiKeyScope, MessageKey> = {
  read: 'apiKeys.scopeRead',
  write: 'apiKeys.scopeWrite',
  admin: 'apiKeys.scopeAdmin',
}

const SCOPE_HINTS: Record<ApiKeyScope, MessageKey> = {
  read: 'apiKeys.scopeHint.read',
  write: 'apiKeys.scopeHint.write',
  admin: 'apiKeys.scopeHint.admin',
}

const EXPIRY_OPTIONS: { label: MessageKey; days: number | null }[] = [
  { label: 'apiKeys.expiry30', days: 30 },
  { label: 'apiKeys.expiry90', days: 90 },
  { label: 'apiKeys.expiryNever', days: null },
]

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  isAdmin: boolean
}

/** #988 — a person's API keys for scripts and services outside the browser. */
export default function ApiKeysDialog({ open, onOpenChange, isAdmin }: Props) {
  const { locale, t } = useLocale()
  const { confirm } = useFeedback()
  const [keys, setKeys] = useState<ApiKey[]>([])
  const [name, setName] = useState('')
  const [scope, setScope] = useState<ApiKeyScope>('read')
  const [expiryDays, setExpiryDays] = useState<number | null>(90)
  // The plaintext key exists only in the creation response; keep it just
  // long enough to copy and drop it when the dialog closes.
  const [freshKey, setFreshKey] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const reload = useCallback(async () => {
    try {
      setLoading(true)
      const resp = await apiFetch('/api/v1/auth/api-keys')
      if (!resp.ok) throw new Error(t('apiKeys.loadFailed', { status: resp.status }))
      setKeys(await resp.json())
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [t])

  useEffect(() => {
    setFreshKey(null)
    setCopied(false)
    if (open) void reload()
  }, [open, reload])

  const handleCreate = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!name.trim()) {
      setError(t('apiKeys.nameRequired'))
      return
    }
    try {
      setLoading(true)
      setError(null)
      const resp = await apiFetch('/api/v1/auth/api-keys', {
        method: 'POST',
        body: JSON.stringify({ name: name.trim(), scope, expires_in_days: expiryDays }),
      })
      if (!resp.ok) {
        const detail = (await resp.json().catch(() => null))?.detail
        throw new Error(typeof detail === 'string' ? detail : t('apiKeys.createFailed', { status: resp.status }))
      }
      const created = await resp.json()
      setFreshKey(created.key)
      setCopied(false)
      setName('')
      await reload()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  const handleRevoke = async (key: ApiKey) => {
    const ok = await confirm({
      title: t('apiKeys.revoke'),
      description: t('apiKeys.revokeConfirm', { name: key.name }),
      confirmLabel: t('apiKeys.revoke'),
      destructive: true,
    })
    if (!ok) return
    try {
      setLoading(true)
      const resp = await apiFetch(`/api/v1/auth/api-keys/${key.id}`, { method: 'DELETE' })
      if (!resp.ok) throw new Error(t('apiKeys.revokeFailed', { status: resp.status }))
      await reload()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  const handleCopy = async () => {
    if (!freshKey) return
    try {
      await navigator.clipboard.writeText(freshKey)
      setCopied(true)
    } catch {
      // Clipboard may be denied (insecure context); the field stays selectable.
    }
  }

  const formatDate = (value: string) =>
    new Date(value).toLocaleDateString(locale === 'ko' ? 'ko-KR' : 'en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[calc(100dvh-2rem)] max-w-lg overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{t('apiKeys.title')}</DialogTitle>
          <DialogDescription>{t('apiKeys.description')}</DialogDescription>
        </DialogHeader>

        <form className="space-y-3" onSubmit={handleCreate}>
          <div className="space-y-1">
            <Label htmlFor="api-key-name">{t('apiKeys.name')}</Label>
            <Input
              id="api-key-name"
              value={name}
              maxLength={100}
              placeholder={t('apiKeys.namePlaceholder')}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="space-y-1">
              <Label htmlFor="api-key-scope">{t('apiKeys.scope')}</Label>
              <Select
                id="api-key-scope"
                value={scope}
                onChange={(e) => setScope(e.target.value as ApiKeyScope)}
              >
                <option value="read">{t('apiKeys.scopeRead')}</option>
                <option value="write">{t('apiKeys.scopeWrite')}</option>
                {isAdmin && <option value="admin">{t('apiKeys.scopeAdmin')}</option>}
              </Select>
            </div>
            <div className="space-y-1">
              <Label htmlFor="api-key-expiry">{t('apiKeys.expiry')}</Label>
              <Select
                id="api-key-expiry"
                value={expiryDays === null ? 'never' : String(expiryDays)}
                onChange={(e) => setExpiryDays(e.target.value === 'never' ? null : Number(e.target.value))}
              >
                {EXPIRY_OPTIONS.map((opt) => (
                  <option key={opt.label} value={opt.days === null ? 'never' : String(opt.days)}>
                    {t(opt.label)}
                  </option>
                ))}
              </Select>
            </div>
          </div>
          <p className="text-sm text-[var(--color-foreground-muted)]">{t(SCOPE_HINTS[scope])}</p>
          <Button type="submit" disabled={loading}>
            {t('apiKeys.create')}
          </Button>
        </form>

        {freshKey && (
          <div className="space-y-2 rounded-[var(--radius-md)] border border-[var(--color-border-strong)] bg-[var(--color-surface-alt)] p-3">
            <p className="text-sm font-medium text-[var(--color-foreground)]">{t('apiKeys.copyNow')}</p>
            <p className="text-sm text-[var(--color-foreground-muted)]">{t('apiKeys.copyWarning')}</p>
            <Input
              readOnly
              value={freshKey}
              aria-label={t('apiKeys.newKey')}
              className="font-mono"
              onFocus={(e) => e.target.select()}
            />
            <Button variant="outline" size="sm" onClick={handleCopy}>
              {copied ? t('common.copied') : t('common.copy')}
            </Button>
          </div>
        )}

        {error && (
          <p role="alert" className="text-sm text-[var(--color-danger)]">{error}</p>
        )}

        <div className="space-y-2">
          <h3 className="text-sm font-medium text-[var(--color-foreground)]">{t('apiKeys.activeKeys')}</h3>
          {keys.length === 0 && !loading && (
            <p className="text-sm text-[var(--color-foreground-muted)]">{t('apiKeys.empty')}</p>
          )}
          <ul className="space-y-1">
            {keys.map((key) => (
              <li
                key={key.id}
                className="flex items-center justify-between gap-2 rounded-[var(--radius-sm)] border border-[var(--color-border)] px-3 py-2"
              >
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm text-[var(--color-foreground)]">
                    {key.name} · {t(SCOPE_LABELS[key.scope])}
                  </div>
                  <div className="truncate text-xs text-[var(--color-foreground-muted)]">
                    <span className="font-mono">{key.prefix}…</span>
                    {' · '}
                    {key.last_used_at
                      ? t('apiKeys.lastUsed', { date: formatDate(key.last_used_at) })
                      : t('apiKeys.neverUsed')}
                    {' · '}
                    {key.expires_at
                      ? t('apiKeys.expires', { date: formatDate(key.expires_at) })
                      : t('apiKeys.noExpiry')}
                  </div>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={loading}
                  aria-label={t('apiKeys.revokeNamed', { name: key.name })}
                  onClick={() => void handleRevoke(key)}
                >
                  {t('apiKeys.revoke')}
                </Button>
              </li>
            ))}
          </ul>
        </div>
      </DialogContent>
    </Dialog>
  )
}

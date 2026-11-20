import { useCallback, useEffect, useId, useMemo, useState, type FormEvent } from 'react'
import { Plus, RefreshCw, Users } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import { useFeedback } from '@/components/feedback/FeedbackProvider'
import { useAuth } from '@/hooks/useAuth'
import { apiFetch } from '@/lib/api'
import { useLocale } from '@/i18n/LocaleProvider'
import { cn } from '@/lib/utils'

/**
 * AUTH-17/AUTH-20 — admin account management (#1058).
 *
 * Accounts are suspended, never deleted: rooms, messages and tasks keep
 * pointing at them. The server refuses to suspend or demote the caller;
 * the UI disables those actions on the admin's own row and says why.
 */

// Mirrors ``AdminUserOut`` in packages/cluster/anygarden/api/v1/users.py.
export interface AdminUser {
  id: string
  email: string
  is_admin: boolean
  disabled: boolean
  created_at: string
  last_login_at: string | null
}

const MIN_PASSWORD_LENGTH = 8

async function errorDetail(resp: Response): Promise<string> {
  const detail = (await resp.json().catch(() => null))?.detail
  return typeof detail === 'string' ? detail : String(resp.status)
}

export default function AdminUsers() {
  const { user } = useAuth()
  if (!user?.is_admin) return null
  return <AdminUsersContent currentUserId={user.id} />
}

function AdminUsersContent({ currentUserId }: { currentUserId: string }) {
  const { t, formatDate } = useLocale()
  const { confirm, notify } = useFeedback()
  const [users, setUsers] = useState<AdminUser[]>([])
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading')
  const [loadError, setLoadError] = useState(0)
  const [actionError, setActionError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [registrationOpen, setRegistrationOpen] = useState<boolean | null>(null)
  const [creating, setCreating] = useState(false)
  const [resetTarget, setResetTarget] = useState<AdminUser | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const searchId = useId()
  const registrationId = useId()
  const registrationHintId = useId()

  const reload = useCallback(async () => {
    setStatus('loading')
    try {
      const resp = await apiFetch('/api/v1/admin/users')
      if (!resp.ok) {
        setLoadError(resp.status)
        setStatus('error')
        return
      }
      setUsers(await resp.json())
      setStatus('ready')
    } catch {
      setLoadError(0)
      setStatus('error')
    }
  }, [])

  useEffect(() => {
    void reload()
    apiFetch('/api/v1/admin/account-settings')
      .then(async resp => {
        if (resp.ok) setRegistrationOpen((await resp.json()).registration_open)
      })
      .catch(() => {})
  }, [reload])

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return needle ? users.filter(u => u.email.toLowerCase().includes(needle)) : users
  }, [users, query])

  const patchUser = useCallback(async (target: AdminUser, body: Partial<Pick<AdminUser, 'is_admin' | 'disabled'>>) => {
    setActionError(null)
    setBusyId(target.id)
    try {
      const resp = await apiFetch(`/api/v1/admin/users/${target.id}`, {
        method: 'PATCH',
        body: JSON.stringify(body),
      })
      if (!resp.ok) {
        setActionError(t('users.actionFailed', { detail: await errorDetail(resp) }))
        return false
      }
      const updated: AdminUser = await resp.json()
      setUsers(rows => rows.map(row => (row.id === updated.id ? updated : row)))
      return true
    } catch (err) {
      setActionError(t('users.actionFailed', { detail: err instanceof Error ? err.message : String(err) }))
      return false
    } finally {
      setBusyId(null)
    }
  }, [t])

  const toggleAdmin = useCallback(async (target: AdminUser) => {
    const granting = !target.is_admin
    const ok = await confirm({
      title: t(granting ? 'users.makeAdminTitle' : 'users.removeAdminTitle'),
      description: t(granting ? 'users.makeAdminConfirm' : 'users.removeAdminConfirm', { email: target.email }),
      confirmLabel: t(granting ? 'users.makeAdmin' : 'users.removeAdmin'),
      destructive: !granting,
    })
    if (ok) await patchUser(target, { is_admin: granting })
  }, [confirm, patchUser, t])

  const toggleDisabled = useCallback(async (target: AdminUser) => {
    if (target.disabled) {
      if (await patchUser(target, { disabled: false })) {
        notify({ message: t('users.restored', { email: target.email }), tone: 'success' })
      }
      return
    }
    const ok = await confirm({
      title: t('users.suspendTitle'),
      description: t('users.suspendConfirm', { email: target.email }),
      confirmLabel: t('users.suspend'),
      destructive: true,
    })
    if (ok) await patchUser(target, { disabled: true })
  }, [confirm, notify, patchUser, t])

  const saveRegistration = useCallback(async (open: boolean) => {
    setActionError(null)
    const previous = registrationOpen
    setRegistrationOpen(open)
    try {
      const resp = await apiFetch('/api/v1/admin/account-settings', {
        method: 'PUT',
        body: JSON.stringify({ registration_open: open }),
      })
      if (!resp.ok) throw new Error(String(resp.status))
      setRegistrationOpen((await resp.json()).registration_open)
    } catch (err) {
      setRegistrationOpen(previous)
      setActionError(t('users.registrationSaveFailed', { status: err instanceof Error ? err.message : '0' }))
    }
  }, [registrationOpen, t])

  const dateText = (value: string) => formatDate(new Date(value), { dateStyle: 'medium' })

  return (
    <div className="mx-auto w-full max-w-4xl space-y-6 px-4 py-5 sm:px-6 sm:py-6">
      <header className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <h1 className="text-heading text-[var(--color-foreground)]">{t('users.title')}</h1>
          <p className="mt-1 text-caption font-normal text-[var(--color-foreground-muted)]">
            {t('users.description')}
          </p>
        </div>
        <div className="flex w-full flex-wrap items-center gap-2 lg:w-auto lg:shrink-0 lg:justify-end">
          <Button variant="ghost" size="sm" onClick={() => void reload()} disabled={status === 'loading'}>
            <RefreshCw className={cn('mr-1 h-3.5 w-3.5', status === 'loading' && 'animate-spin')} aria-hidden="true" />
            {t('common.refresh')}
          </Button>
          <Button size="sm" className="order-first w-full sm:w-auto lg:order-last" onClick={() => setCreating(true)}>
            <Plus className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
            {t('users.newUser')}
          </Button>
        </div>
      </header>

      {actionError && (
        <div role="alert" className="rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-[var(--color-danger-soft)] p-3 text-caption font-medium text-[var(--color-danger)]">
          {actionError}
        </div>
      )}

      {registrationOpen !== null && (
        <section aria-labelledby={`${registrationId}-heading`} className="space-y-2">
          <h2 id={`${registrationId}-heading`} className="text-caption font-semibold text-[var(--color-foreground)]">
            {t('users.registration')}
          </h2>
          <div className="flex items-start gap-3 rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-4 py-3 shadow-whisper">
            <input
              id={registrationId}
              type="checkbox"
              checked={registrationOpen}
              onChange={e => void saveRegistration(e.target.checked)}
              aria-describedby={registrationHintId}
              data-testid="users-registration-open"
              className="mt-0.5 size-4 shrink-0 accent-[var(--color-brand)]"
            />
            <div className="min-w-0 space-y-1">
              <Label htmlFor={registrationId}>{t('users.registrationOpen')}</Label>
              <p id={registrationHintId} className="text-caption text-[var(--color-foreground-muted)]">
                {t(registrationOpen ? 'users.registrationOpenHint' : 'users.registrationClosedHint')}
              </p>
            </div>
          </div>
        </section>
      )}

      <section aria-labelledby="users-list-heading" className="space-y-3">
        <h2 id="users-list-heading" className="sr-only">{t('users.title')}</h2>
        <div className="max-w-sm">
          <Label htmlFor={searchId} className="sr-only">{t('users.searchLabel')}</Label>
          <Input
            id={searchId}
            type="search"
            value={query}
            placeholder={t('users.searchPlaceholder')}
            onChange={e => setQuery(e.target.value)}
          />
        </div>

        {status === 'error' ? (
          <div role="alert" className="rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-[var(--color-danger-soft)] p-3 text-caption font-medium text-[var(--color-danger)]">
            {t('users.loadFailed', { status: loadError })}
          </div>
        ) : status === 'loading' && users.length === 0 ? (
          <p role="status" className="text-caption font-medium text-[var(--color-foreground-muted)]">{t('users.loading')}</p>
        ) : visible.length === 0 ? (
          <div className="rounded-[var(--radius-lg)] border border-dashed border-[var(--color-border)] px-6 py-10 text-center">
            <Users className="mx-auto mb-3 h-8 w-8 text-[var(--color-foreground-subtle)]" strokeWidth={1.5} aria-hidden="true" />
            <p className="text-caption font-medium text-[var(--color-foreground-muted)]">{t('users.noMatch', { query: query.trim() })}</p>
          </div>
        ) : (
          <ul className="divide-y divide-[var(--color-border)] rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-surface-elevated)] shadow-whisper">
            {visible.map(row => {
              const self = row.id === currentUserId
              const busy = busyId === row.id
              const adminAction = t(row.is_admin ? 'users.removeAdmin' : 'users.makeAdmin')
              const disableAction = t(row.disabled ? 'users.restore' : 'users.suspend')
              return (
                <li key={row.id} data-testid={`user-row-${row.email}`} className="flex flex-col gap-3 px-4 py-3 lg:flex-row lg:items-center lg:justify-between">
                  <div className="min-w-0 space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className={cn('min-w-0 break-all text-caption font-medium', row.disabled ? 'text-[var(--color-foreground-muted)]' : 'text-[var(--color-foreground)]')}>
                        {row.email}
                      </span>
                      {self && <Badge variant="outline">{t('users.you')}</Badge>}
                      <Badge variant={row.is_admin ? 'default' : 'secondary'}>
                        {t(row.is_admin ? 'users.roleAdmin' : 'users.roleMember')}
                      </Badge>
                      {row.disabled && <Badge variant="destructive">{t('users.statusDisabled')}</Badge>}
                    </div>
                    <p className="text-badge text-[var(--color-foreground-muted)]">
                      {t('users.joined', { date: dateText(row.created_at) })}
                      {' · '}
                      {row.last_login_at
                        ? t('users.lastLogin', { date: dateText(row.last_login_at) })
                        : t('users.neverLoggedIn')}
                    </p>
                    {self && (
                      <p className="text-badge text-[var(--color-foreground-muted)]">{t('users.selfLocked')}</p>
                    )}
                  </div>
                  <div className="flex flex-wrap items-center gap-2 lg:shrink-0 lg:justify-end">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={self || busy}
                      aria-label={t('users.actionLabel', { action: adminAction, email: row.email })}
                      onClick={() => void toggleAdmin(row)}
                    >
                      {adminAction}
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={busy}
                      aria-label={t('users.actionLabel', { action: t('users.resetPassword'), email: row.email })}
                      onClick={() => setResetTarget(row)}
                    >
                      {t('users.resetPassword')}
                    </Button>
                    <Button
                      variant={row.disabled ? 'outline' : 'destructive'}
                      size="sm"
                      disabled={self || busy}
                      aria-label={t('users.actionLabel', { action: disableAction, email: row.email })}
                      onClick={() => void toggleDisabled(row)}
                    >
                      {disableAction}
                    </Button>
                  </div>
                </li>
              )
            })}
          </ul>
        )}
      </section>

      <CreateUserDialog
        open={creating}
        onOpenChange={setCreating}
        onCreated={created => {
          setUsers(rows => [...rows, created])
          notify({ message: t('users.created', { email: created.email }), tone: 'success' })
        }}
      />
      <ResetPasswordDialog
        target={resetTarget}
        onClose={() => setResetTarget(null)}
        onDone={email => notify({ message: t('users.resetDone', { email }), tone: 'success' })}
      />
    </div>
  )
}

function CreateUserDialog({ open, onOpenChange, onCreated }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreated: (user: AdminUser) => void
}) {
  const { t } = useLocale()
  const ids = { email: useId(), password: useId(), passwordHint: useId(), admin: useId(), adminHint: useId() }
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [isAdmin, setIsAdmin] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setEmail('')
    setPassword('')
    setIsAdmin(false)
    setError(null)
  }, [open])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setSaving(true)
    setError(null)
    try {
      const resp = await apiFetch('/api/v1/admin/users', {
        method: 'POST',
        body: JSON.stringify({ email: email.trim(), password, is_admin: isAdmin }),
      })
      if (!resp.ok) {
        setError(resp.status === 409 ? t('users.emailTaken') : t('users.createFailed', { status: resp.status }))
        return
      }
      onCreated(await resp.json())
      onOpenChange(false)
    } catch {
      setError(t('users.createFailed', { status: 0 }))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[calc(100dvh-2rem)] max-w-md overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{t('users.createTitle')}</DialogTitle>
          <DialogDescription>{t('users.createDescription')}</DialogDescription>
        </DialogHeader>
        <form className="space-y-4" onSubmit={submit}>
          <div className="space-y-2">
            <Label htmlFor={ids.email}>{t('users.email')}</Label>
            <Input
              id={ids.email}
              type="email"
              autoComplete="off"
              required
              value={email}
              onChange={e => setEmail(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor={ids.password}>{t('users.initialPassword')}</Label>
            <Input
              id={ids.password}
              type="password"
              autoComplete="new-password"
              required
              minLength={MIN_PASSWORD_LENGTH}
              aria-describedby={ids.passwordHint}
              value={password}
              onChange={e => setPassword(e.target.value)}
            />
            <p id={ids.passwordHint} className="text-caption text-[var(--color-foreground-muted)]">{t('users.passwordHint')}</p>
          </div>
          <div className="flex items-start gap-3">
            <input
              id={ids.admin}
              type="checkbox"
              checked={isAdmin}
              onChange={e => setIsAdmin(e.target.checked)}
              aria-describedby={ids.adminHint}
              className="mt-0.5 size-4 shrink-0 accent-[var(--color-brand)]"
            />
            <div className="min-w-0 space-y-1">
              <Label htmlFor={ids.admin}>{t('users.grantAdmin')}</Label>
              <p id={ids.adminHint} className="text-caption text-[var(--color-foreground-muted)]">{t('users.grantAdminHint')}</p>
            </div>
          </div>
          {error && <p role="alert" className="text-caption text-[var(--color-danger)]">{error}</p>}
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>{t('common.cancel')}</Button>
            <Button type="submit" disabled={saving}>{saving ? t('users.creating') : t('users.create')}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function ResetPasswordDialog({ target, onClose, onDone }: {
  target: AdminUser | null
  onClose: () => void
  onDone: (email: string) => void
}) {
  const { t } = useLocale()
  const passwordId = useId()
  const hintId = useId()
  const [password, setPassword] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setPassword('')
    setError(null)
  }, [target])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!target) return
    setSaving(true)
    setError(null)
    try {
      const resp = await apiFetch(`/api/v1/admin/users/${target.id}/password`, {
        method: 'POST',
        body: JSON.stringify({ password }),
      })
      if (!resp.ok) {
        setError(t('users.resetFailed', { status: resp.status }))
        return
      }
      onDone(target.email)
      onClose()
    } catch {
      setError(t('users.resetFailed', { status: 0 }))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={target !== null} onOpenChange={open => { if (!open) onClose() }}>
      <DialogContent className="max-h-[calc(100dvh-2rem)] max-w-md overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{t('users.resetTitle')}</DialogTitle>
          <DialogDescription>{t('users.resetDescription', { email: target?.email ?? '' })}</DialogDescription>
        </DialogHeader>
        <form className="space-y-4" onSubmit={submit}>
          <div className="space-y-2">
            <Label htmlFor={passwordId}>{t('users.newPassword')}</Label>
            <Input
              id={passwordId}
              type="password"
              autoComplete="new-password"
              required
              minLength={MIN_PASSWORD_LENGTH}
              aria-describedby={hintId}
              value={password}
              onChange={e => setPassword(e.target.value)}
            />
            <p id={hintId} className="text-caption text-[var(--color-foreground-muted)]">{t('users.passwordHint')}</p>
          </div>
          {error && <p role="alert" className="text-caption text-[var(--color-danger)]">{error}</p>}
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={onClose}>{t('common.cancel')}</Button>
            <Button type="submit" disabled={saving}>{saving ? t('account.saving') : t('users.reset')}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

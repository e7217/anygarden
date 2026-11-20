import { useEffect, useId, useState, type FormEvent } from 'react'
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useFeedback } from '@/components/feedback/FeedbackProvider'
import { apiFetch } from '@/lib/api'
import { setRegisteredToken } from '@/lib/authStorage'
import { useLocale } from '@/i18n/LocaleProvider'

const MIN_PASSWORD_LENGTH = 8

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
}

/** AUTH-21 — change your own password; other sessions of the account end. */
export default function ChangePasswordDialog({ open, onOpenChange }: Props) {
  const { t } = useLocale()
  const { notify } = useFeedback()
  const ids = { current: useId(), next: useId(), confirm: useId(), hint: useId() }
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setCurrent('')
    setNext('')
    setConfirmation('')
    setError(null)
  }, [open])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (next !== confirmation) {
      setError(t('account.passwordMismatch'))
      return
    }
    setSaving(true)
    setError(null)
    try {
      const resp = await apiFetch('/api/v1/auth/password', {
        method: 'POST',
        body: JSON.stringify({ current_password: current, new_password: next }),
      })
      if (!resp.ok) {
        setError(resp.status === 400 ? t('account.wrongCurrent') : t('account.changeFailed', { status: resp.status }))
        return
      }
      // The old token stops working the moment the password changes.
      setRegisteredToken((await resp.json()).token)
      notify({ message: t('account.changed'), tone: 'success' })
      onOpenChange(false)
    } catch {
      setError(t('account.changeFailed', { status: 0 }))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[calc(100dvh-2rem)] max-w-md overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{t('account.changePassword')}</DialogTitle>
          <DialogDescription>{t('account.changePasswordDescription')}</DialogDescription>
        </DialogHeader>
        <form className="space-y-4" onSubmit={submit}>
          <div className="space-y-2">
            <Label htmlFor={ids.current}>{t('account.currentPassword')}</Label>
            <Input
              id={ids.current}
              type="password"
              autoComplete="current-password"
              required
              value={current}
              onChange={e => setCurrent(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor={ids.next}>{t('account.newPassword')}</Label>
            <Input
              id={ids.next}
              type="password"
              autoComplete="new-password"
              required
              minLength={MIN_PASSWORD_LENGTH}
              aria-describedby={ids.hint}
              value={next}
              onChange={e => setNext(e.target.value)}
            />
            <p id={ids.hint} className="text-caption text-[var(--color-foreground-muted)]">{t('auth.passwordHint')}</p>
          </div>
          <div className="space-y-2">
            <Label htmlFor={ids.confirm}>{t('account.confirmPassword')}</Label>
            <Input
              id={ids.confirm}
              type="password"
              autoComplete="new-password"
              required
              minLength={MIN_PASSWORD_LENGTH}
              value={confirmation}
              onChange={e => setConfirmation(e.target.value)}
            />
          </div>
          {error && <p role="alert" className="text-caption text-[var(--color-danger)]">{error}</p>}
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>{t('common.cancel')}</Button>
            <Button type="submit" disabled={saving}>{saving ? t('account.saving') : t('account.changePassword')}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

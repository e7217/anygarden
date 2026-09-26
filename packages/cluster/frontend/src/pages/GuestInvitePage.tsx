import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { getAuthToken, setGuestToken } from '@/lib/authStorage'
import { useLocale } from '@/i18n/LocaleProvider'
import { LocaleToggle } from '@/i18n/LocaleToggle'
import { ThemeToggle } from '@/theme/ThemeToggle'

/**
 * ``/invite/:token``
 *
 * Guest entry flow. Accepts the token emitted by ``RoomInviteDialog``
 * (${origin}/invite/{encoded_token}) — we ``decodeURIComponent`` it
 * because the encode step is owned by PR F. A signed-in real user
 * who lands here will *overwrite* their session when they accept;
 * this matches the single-tab-session architecture and we warn
 * inline. A more flexible "dual session" story is deferred per §11.11.
 */
export default function GuestInvitePage() {
  const { t } = useLocale()
  const { token: rawToken } = useParams<{ token: string }>()
  const navigate = useNavigate()
  const [displayName, setDisplayName] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const hadPriorSession = Boolean(getAuthToken())
  // Tokens emitted by PR F are ``encodeURIComponent`` wrapped.
  // ``decodeURIComponent`` is idempotent for the urlsafe alphabet.
  const token = rawToken ? decodeURIComponent(rawToken) : ''

  const handleAccept = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)

    const trimmed = displayName.trim()
    if (trimmed.length < 1 || trimmed.length > 64) {
      setError(t('guest.displayNameInvalid'))
      return
    }

    setSubmitting(true)
    try {
      const resp = await fetch('/api/v1/auth/guest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token, display_name: trimmed }),
      })
      if (!resp.ok) {
        throw new Error(t('guest.joinFailed'))
      }
      const data = await resp.json()
      // Single-tab architecture: the guest JWT overwrites the
      // ``anygarden_token`` slot. The storage helper keeps the legacy
      // prelogin stash for compatibility, but normal login and guest
      // expiry paths clear it so an old token cannot be restored.
      setGuestToken({
        token: data.token,
        roomId: data.room_id,
        displayName: data.display_name ?? trimmed,
      })
      navigate(`/g/${data.room_id}`, { replace: true })
    } catch {
      setError(t('guest.joinFailed'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="relative flex min-h-dvh items-center justify-center bg-[var(--color-surface-alt)] p-4 py-16">
      <div className="absolute right-4 top-4 flex items-center gap-2">
        <LocaleToggle compact />
        <ThemeToggle />
      </div>
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>{t('guest.joinTitle')}</CardTitle>
          <CardDescription>{t('guest.joinDescription')}</CardDescription>
        </CardHeader>
        <CardContent>
          {hadPriorSession && (
            <div className="mb-4 rounded-[var(--radius-md)] border border-[color:color-mix(in_srgb,var(--color-warning)_30%,transparent)] bg-[color:color-mix(in_srgb,var(--color-warning)_10%,transparent)] px-3 py-2 text-xs text-[var(--color-warning)]">
              {t('guest.priorSession')}
            </div>
          )}
          <form onSubmit={handleAccept} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="guest-display-name">{t('guest.displayName')}</Label>
              <Input
                id="guest-display-name"
                autoFocus
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder={t('guest.displayNamePlaceholder')}
                maxLength={64}
              />
            </div>
            {error && (
              <div role="alert" className="rounded-[var(--radius-md)] border border-[color:color-mix(in_srgb,var(--color-warning)_30%,transparent)] bg-[color:color-mix(in_srgb,var(--color-warning)_10%,transparent)] px-3 py-2 text-sm text-[var(--color-warning)]">
                {error}
              </div>
            )}
            <Button
              type="submit"
              className="w-full"
              disabled={submitting || !token}
            >
              {submitting ? t('guest.joining') : t('guest.join')}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}

import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

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
  const { token: rawToken } = useParams<{ token: string }>()
  const navigate = useNavigate()
  const [displayName, setDisplayName] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const hadPriorSession = Boolean(localStorage.getItem('doorae_token'))
  // Tokens emitted by PR F are ``encodeURIComponent`` wrapped.
  // ``decodeURIComponent`` is idempotent for the urlsafe alphabet.
  const token = rawToken ? decodeURIComponent(rawToken) : ''

  const handleAccept = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)

    const trimmed = displayName.trim()
    if (trimmed.length < 1 || trimmed.length > 64) {
      setError('Display name must be 1–64 characters.')
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
        const body = await resp.json().catch(() => ({}))
        throw new Error(body.detail || 'Could not accept invite')
      }
      const data = await resp.json()
      // Single-tab architecture: the guest JWT overwrites the
      // ``doorae_token`` slot. To avoid silently destroying a prior
      // registered-user session, stash it under
      // ``doorae_token_prelogin`` so the guest-logout path can
      // restore it. If the guest never logs out (closes tab) the
      // stash just sits there harmlessly — next real login overwrites.
      const prior = localStorage.getItem('doorae_token')
      if (prior && localStorage.getItem('doorae_is_guest') !== '1') {
        localStorage.setItem('doorae_token_prelogin', prior)
      }
      localStorage.setItem('doorae_token', data.token)
      localStorage.setItem('doorae_is_guest', '1')
      localStorage.setItem('doorae_guest_room_id', data.room_id)
      localStorage.setItem('doorae_guest_display_name', data.display_name ?? trimmed)
      navigate(`/g/${data.room_id}`, { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--color-background)] p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Join as a guest</CardTitle>
          <CardDescription>
            Choose a display name. Guests can read and send messages in
            the invited room, and mention agents by name.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {hadPriorSession && (
            <div className="mb-4 rounded-[var(--radius-md)] border border-[color:color-mix(in_srgb,var(--color-warning)_30%,transparent)] bg-[color:color-mix(in_srgb,var(--color-warning)_10%,transparent)] px-3 py-2 text-xs text-[var(--color-warning)]">
              You're already signed in. Joining as a guest will sign you
              out of that account in this tab.
            </div>
          )}
          <form onSubmit={handleAccept} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="guest-display-name">Display name</Label>
              <Input
                id="guest-display-name"
                autoFocus
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="e.g. Jamie"
                maxLength={64}
              />
            </div>
            {error && (
              <div className="rounded-[var(--radius-md)] border border-[color:color-mix(in_srgb,var(--color-warning)_30%,transparent)] bg-[color:color-mix(in_srgb,var(--color-warning)_10%,transparent)] px-3 py-2 text-sm text-[var(--color-warning)]">
                {error}
              </div>
            )}
            <Button
              type="submit"
              className="w-full"
              disabled={submitting || !token}
            >
              {submitting ? 'Joining…' : 'Join room'}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}

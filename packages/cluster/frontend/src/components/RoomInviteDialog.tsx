import { useCallback, useEffect, useState } from 'react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { apiFetch } from '@/lib/api'

// Keep these in sync with packages/cluster/anygarden/api/v1/invites.py.
// ``InviteCreate`` validates the same bounds server-side; enforcing
// them here is purely for UX (so we can block disabled form
// submission rather than waiting for a 422).
const MIN_EXPIRY_SECONDS = 60
const MAX_EXPIRY_SECONDS = 60 * 60 * 24 * 30

// Display format returned by ``GET /api/v1/rooms/{id}/invites``.
interface InviteRow {
  id: string
  room_id: string
  created_by_user_id: string
  created_at: string
  expires_at: string | null
  revoked_at: string | null
  max_uses: number | null
  use_count: number
}

interface Props {
  roomId: string
  open: boolean
  onOpenChange: (open: boolean) => void
}

// Preset durations mirror what the server accepts. ``null`` ⇒ "no
// expiry" so an admin can explicitly opt into indefinite links.
const EXPIRY_OPTIONS: { label: string; seconds: number | null }[] = [
  { label: '1 hour', seconds: 60 * 60 },
  { label: '24 hours', seconds: 60 * 60 * 24 },
  { label: '7 days', seconds: 60 * 60 * 24 * 7 },
  { label: 'Never', seconds: null },
]

export default function RoomInviteDialog({ roomId, open, onOpenChange }: Props) {
  const [invites, setInvites] = useState<InviteRow[]>([])
  const [expirySeconds, setExpirySeconds] = useState<number | null>(60 * 60 * 24)
  const [maxUses, setMaxUses] = useState<string>('')
  // Plaintext token only returned at creation; kept in state just
  // long enough for the admin to copy it. We never persist it.
  const [freshToken, setFreshToken] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const reload = useCallback(async () => {
    if (!roomId) return
    try {
      setLoading(true)
      const resp = await apiFetch(`/api/v1/rooms/${roomId}/invites`)
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}))
        throw new Error(body.detail || `Failed to load invites (${resp.status})`)
      }
      setInvites(await resp.json())
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [roomId])

  useEffect(() => {
    if (open) {
      setFreshToken(null)
      void reload()
    } else {
      // Clear the one-time token the moment the dialog closes so it
      // isn't left lingering in component state / React devtools.
      // The hash-backed invite on the server is unaffected.
      setFreshToken(null)
    }
  }, [open, reload])

  const handleCreate = async () => {
    setError(null)
    const trimmed = maxUses.trim()
    const parsedMaxUses = trimmed === '' ? null : Number(trimmed)
    if (parsedMaxUses !== null && !Number.isInteger(parsedMaxUses)) {
      setError('Max uses must be a positive integer or blank.')
      return
    }
    if (parsedMaxUses !== null && (parsedMaxUses < 1 || parsedMaxUses > 1000)) {
      setError('Max uses must be between 1 and 1000.')
      return
    }
    if (
      expirySeconds !== null &&
      (expirySeconds < MIN_EXPIRY_SECONDS || expirySeconds > MAX_EXPIRY_SECONDS)
    ) {
      setError('Expiry is outside the allowed range.')
      return
    }

    try {
      setLoading(true)
      const resp = await apiFetch(`/api/v1/rooms/${roomId}/invites`, {
        method: 'POST',
        body: JSON.stringify({
          expires_in_seconds: expirySeconds,
          max_uses: parsedMaxUses,
        }),
      })
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}))
        throw new Error(body.detail || `Failed to create invite (${resp.status})`)
      }
      const created = await resp.json()
      setFreshToken(created.token)
      await reload()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  const handleRevoke = async (inviteId: string) => {
    setError(null)
    try {
      setLoading(true)
      const resp = await apiFetch(`/api/v1/invites/${inviteId}`, {
        method: 'DELETE',
      })
      if (!resp.ok && resp.status !== 204) {
        const body = await resp.json().catch(() => ({}))
        throw new Error(body.detail || `Failed to revoke (${resp.status})`)
      }
      await reload()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  const handleCopyToken = async () => {
    if (!freshToken) return
    try {
      await navigator.clipboard.writeText(inviteUrl(freshToken))
    } catch {
      // Clipboard may be denied (insecure context); the textarea
      // below remains selectable so the admin can copy manually.
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Invite links</DialogTitle>
          <DialogDescription>
            Share a guest link so non-members can join this room without
            creating an account. Guests can only read and send messages
            in this room.
          </DialogDescription>
        </DialogHeader>

        {/* Create form */}
        <div className="space-y-4 py-2">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label>Expiry</Label>
              <select
                value={expirySeconds === null ? 'never' : String(expirySeconds)}
                onChange={(e) => {
                  const v = e.target.value
                  setExpirySeconds(v === 'never' ? null : Number(v))
                }}
                className="h-9 w-full rounded-[var(--radius-md)] border border-[var(--color-border)] bg-white px-2 text-sm"
              >
                {EXPIRY_OPTIONS.map((opt) => (
                  <option
                    key={opt.label}
                    value={opt.seconds === null ? 'never' : String(opt.seconds)}
                  >
                    {opt.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1">
              <Label htmlFor="invite-max-uses">Max uses (optional)</Label>
              <Input
                id="invite-max-uses"
                type="number"
                min={1}
                max={1000}
                placeholder="Unlimited"
                value={maxUses}
                onChange={(e) => setMaxUses(e.target.value)}
              />
            </div>
          </div>
          <Button onClick={handleCreate} disabled={loading}>
            {loading ? 'Working…' : 'Create invite link'}
          </Button>
        </div>

        {/* Fresh token display (one-time) */}
        {freshToken && (
          <div className="space-y-2 rounded-[var(--radius-md)] border border-[var(--color-brand)] bg-[color:color-mix(in_srgb,var(--color-brand)_8%,transparent)] p-3">
            <div className="text-sm font-medium">Copy this link now</div>
            <div className="text-xs text-[var(--color-foreground-muted)]">
              The server only stores a hash — you won't be able to see this link again.
            </div>
            <textarea
              readOnly
              rows={2}
              value={inviteUrl(freshToken)}
              className="w-full resize-none rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-white px-2 py-1 font-mono text-xs"
              onFocus={(e) => e.target.select()}
            />
            <Button variant="outline" size="sm" onClick={handleCopyToken}>
              Copy link
            </Button>
          </div>
        )}

        {/* Existing invites */}
        <div className="space-y-2">
          <div className="text-sm font-medium">Active invites</div>
          {invites.length === 0 && !loading && (
            <div className="text-xs text-[var(--color-foreground-muted)]">
              No active invites for this room.
            </div>
          )}
          <ul className="space-y-1 max-h-48 overflow-y-auto">
            {invites.map((inv) => (
              <li
                key={inv.id}
                className="flex items-center justify-between gap-2 rounded-[var(--radius-sm)] border border-[var(--color-border)] px-3 py-2 text-xs"
              >
                <div className="min-w-0 flex-1">
                  <div className="truncate font-mono">{inv.id}</div>
                  <div className="text-[var(--color-foreground-muted)]">
                    {describeInvite(inv)}
                  </div>
                </div>
                {!inv.revoked_at && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => handleRevoke(inv.id)}
                    disabled={loading}
                  >
                    Revoke
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </div>

        {error && (
          <div className="rounded-[var(--radius-md)] border border-[color:color-mix(in_srgb,var(--color-warning)_30%,transparent)] bg-[color:color-mix(in_srgb,var(--color-warning)_10%,transparent)] px-3 py-2 text-sm text-[var(--color-warning)]">
            {error}
          </div>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function inviteUrl(token: string): string {
  // Browser URL pattern owned by PR G (guest entry route). Token
  // alphabet is ``secrets.token_urlsafe()`` = [A-Za-z0-9_-], all
  // of which ``encodeURIComponent`` passes through unchanged.
  // **PR G must call ``decodeURIComponent`` on the path param
  // anyway** so any future admin pasting a URL hand-edited with
  // stray characters still resolves cleanly. ``origin`` avoids
  // mis-matching subpath deployments.
  return `${window.location.origin}/invite/${encodeURIComponent(token)}`
}

function describeInvite(inv: InviteRow): string {
  if (inv.revoked_at) {
    return `revoked ${formatRelative(inv.revoked_at)}`
  }
  const parts: string[] = []
  if (inv.expires_at) {
    parts.push(`expires ${formatRelative(inv.expires_at)}`)
  } else {
    parts.push('never expires')
  }
  parts.push(
    inv.max_uses === null
      ? `${inv.use_count} uses`
      : `${inv.use_count} / ${inv.max_uses} uses`,
  )
  return parts.join(' · ')
}

function formatRelative(iso: string): string {
  try {
    const d = new Date(iso)
    return d.toLocaleString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
    })
  } catch {
    return iso
  }
}

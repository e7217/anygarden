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

interface Props {
  roomId: string
  open: boolean
  onOpenChange: (open: boolean) => void
  onSaved?: () => void
}

export default function RoomEditDialog({ roomId, open, onOpenChange, onSaved }: Props) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (!roomId) return
    try {
      const resp = await apiFetch(`/api/v1/rooms/${roomId}`)
      if (!resp.ok) return
      const data = await resp.json()
      setName(data.name ?? '')
      setDescription(data.description ?? '')
      setError(null)
    } catch { /* ignore */ }
  }, [roomId])

  useEffect(() => {
    if (open) void load()
  }, [open, load])

  const handleSave = async () => {
    setSaving(true)
    setError(null)
    try {
      const resp = await apiFetch(`/api/v1/rooms/${roomId}`, {
        method: 'PATCH',
        body: JSON.stringify({
          name: name.trim(),
          description: description.trim() || null,
        }),
      })
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}))
        throw new Error(body.detail || 'Failed to save')
      }
      onSaved?.()
      onOpenChange(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
    setSaving(false)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Edit room</DialogTitle>
          <DialogDescription>Update the room name and description.</DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          <div className="space-y-2">
            <Label htmlFor="room-edit-name">Name</Label>
            <Input
              id="room-edit-name"
              value={name}
              onChange={e => setName(e.target.value)}
              autoFocus
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="room-edit-desc">Description</Label>
            <textarea
              id="room-edit-desc"
              className="flex w-full rounded-[var(--radius-md)] border border-[var(--color-border)] bg-white px-3 py-2 text-sm placeholder:text-[var(--color-foreground-muted)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand)] focus:ring-offset-1 resize-none"
              placeholder="이 룸의 목적을 설명하세요"
              rows={3}
              value={description}
              onChange={e => setDescription(e.target.value)}
            />
          </div>
        </div>

        {error && (
          <div className="rounded-[var(--radius-md)] border border-[color:color-mix(in_srgb,var(--color-warning)_30%,transparent)] bg-[color:color-mix(in_srgb,var(--color-warning)_10%,transparent)] px-3 py-2 text-sm text-[var(--color-warning)]">
            {error}
          </div>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={saving}>Cancel</Button>
          <Button onClick={handleSave} disabled={saving || !name.trim()}>
            {saving ? 'Saving…' : 'Save'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

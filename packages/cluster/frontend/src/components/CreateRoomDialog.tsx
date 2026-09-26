import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter, DialogTrigger,
} from '@/components/ui/dialog'
import { Plus } from 'lucide-react'
import { useLocale } from '@/i18n/LocaleProvider'

interface CreateRoomDialogProps {
  projects: { id: string; name: string }[]
  onCreateRoom: (projectId: string, name: string) => Promise<void>
}

export default function CreateRoomDialog({ projects, onCreateRoom }: CreateRoomDialogProps) {
  const { t } = useLocale()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [projectId, setProjectId] = useState(projects[0]?.id || '')
  const [loading, setLoading] = useState(false)

  const handleCreate = async () => {
    if (!name.trim() || !projectId) return
    setLoading(true)
    try {
      await onCreateRoom(projectId, name.trim())
      setName('')
      setOpen(false)
    } catch { /* ignore */ }
    setLoading(false)
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          <Plus className="mr-2 h-4 w-4" />
          {t('rooms.new')}
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('rooms.createTitle')}</DialogTitle>
          <DialogDescription>
            {t('rooms.createDescription')}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4 py-2">
          {projects.length > 1 && (
            <div className="space-y-2">
              <Label htmlFor="project-select">{t('rooms.project')}</Label>
              <select
                id="project-select"
                value={projectId}
                onChange={e => setProjectId(e.target.value)}
                className="flex h-9 w-full rounded-[var(--radius-xs)] border border-[var(--color-border-strong)] bg-[var(--color-background)] px-3 py-1 text-sm text-[var(--color-foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-brand-focus)]"
              >
                {projects.map(p => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
            </div>
          )}
          <div className="space-y-2">
            <Label htmlFor="room-name">{t('rooms.name')}</Label>
            <Input
              id="room-name"
              placeholder={t('rooms.namePlaceholder')}
              value={name}
              onChange={e => setName(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleCreate()}
            />
          </div>
        </div>
        <DialogFooter>
          <Button onClick={handleCreate} disabled={loading || !name.trim() || !projectId}>
            {loading ? t('rooms.creating') : t('rooms.create')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

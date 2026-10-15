import { useState } from 'react'
import { Loader2, Plus, Unplug } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { useFeedback } from '@/components/feedback/FeedbackProvider'
import { useLocale } from '@/i18n/LocaleProvider'
import type { MessageKey } from '@/i18n/messages'
import { MachineLinkError, useMachineLinks, type MachineLink } from '@/hooks/useMachineLinks'

const ERROR_KEYS: Record<string, MessageKey> = {
  MACHINE_LINK_CODE_INVALID: 'admin.machineLinks.codeInvalid',
  MACHINE_LINK_SELF: 'admin.machineLinks.codeSelf',
  MACHINE_LINK_EXISTS: 'admin.machineLinks.codeExists',
}

function dot(status: MachineLink['status']) {
  if (status === 'connected') return 'bg-[var(--color-success)]'
  if (status === 'connecting') return 'bg-[var(--color-warning)]'
  if (status === 'auth_failed' || status === 'error') return 'bg-[var(--color-destructive)]'
  return 'bg-[var(--color-foreground-subtle)]'
}

/**
 * Rail section on an integrated node: other servers this computer also
 * serves as one of their machines (#848). Hidden on a plain server.
 */
export default function MachineLinksSection() {
  const { t } = useLocale()
  const { confirm, notify } = useFeedback()
  const { links, available, addLink, removeLink } = useMachineLinks()
  const [open, setOpen] = useState(false)
  const [code, setCode] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!available) return null

  const statusLabel = (status: MachineLink['status']) => t(`admin.machineLinks.status.${status}`)
  const hint = (link: MachineLink) => {
    if (link.status === 'auth_failed') return t('admin.machineLinks.authFailedHint')
    if (link.status === 'disconnected') return t('admin.machineLinks.disconnectedHint')
    return null
  }

  async function submit() {
    setSaving(true)
    setError(null)
    try {
      const link = await addLink(code)
      setOpen(false)
      setCode('')
      notify({ message: t('admin.machineLinks.added', { name: link.name }), tone: 'success' })
    } catch (e) {
      const key = e instanceof MachineLinkError && e.code ? ERROR_KEYS[e.code] : undefined
      setError(key ? t(key) : t('admin.machineLinks.addFailed'))
    } finally {
      setSaving(false)
    }
  }

  async function remove(link: MachineLink) {
    const ok = await confirm({
      title: t('admin.machineLinks.removeTitle', { name: link.name }),
      description: t('admin.machineLinks.removeDescription', { server: link.server }),
      confirmLabel: t('admin.machineLinks.remove'),
      destructive: true,
    })
    if (!ok) return
    try {
      await removeLink(link.machine_id)
    } catch (e) {
      const inUse = e instanceof MachineLinkError && e.code === 'MACHINE_LINK_IN_USE'
      notify({ message: t(inUse ? 'admin.machineLinks.inUse' : 'admin.machineLinks.removeFailed'), tone: 'error' })
    }
  }

  return (
    <section aria-labelledby="machine-links-title" className="border-t border-[var(--color-border)] px-4 py-3">
      <div className="flex items-center justify-between gap-2">
        <h2 id="machine-links-title" className="text-sm font-semibold text-[var(--color-foreground)]">{t('admin.machineLinks.title')}</h2>
        <Button variant="ghost" size="sm" onClick={() => { setError(null); setOpen(true) }}>
          <Plus />
          {t('admin.machineLinks.add')}
        </Button>
      </div>
      <p className="mt-1 text-xs leading-relaxed text-[var(--color-foreground-muted)]">{t('admin.machineLinks.purpose')}</p>
      {links.length === 0 ? (
        <p className="mt-2 text-xs text-[var(--color-foreground-subtle)]">{t('admin.machineLinks.none')}</p>
      ) : (
        <ul className="mt-2 space-y-2">
          {links.map(link => (
            <li key={link.machine_id} className="rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-3 py-2">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium text-[var(--color-foreground)]">{link.name}</div>
                  <div className="truncate text-xs text-[var(--color-foreground-muted)]" title={link.server}>{link.server}</div>
                </div>
                <Button variant="ghost" size="icon" onClick={() => void remove(link)} aria-label={t('admin.machineLinks.removeLabel', { name: link.name })}>
                  <Unplug />
                </Button>
              </div>
              <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs">
                <span className="flex items-center gap-1 text-[var(--color-foreground-muted)]">
                  <span aria-hidden="true" className={`inline-block h-1.5 w-1.5 rounded-full ${dot(link.status)}`} />
                  {statusLabel(link.status)}
                </span>
                <span className="text-[var(--color-foreground-subtle)]">{t('admin.machines.agentCount', { count: link.running_agents })}</span>
              </div>
              {hint(link) && <p className="mt-1 text-xs text-[var(--color-foreground-muted)]">{hint(link)}</p>}
              {link.error && link.status !== 'connected' && (
                <p className="mt-1 break-words text-xs text-[var(--color-foreground-subtle)]">{link.error}</p>
              )}
            </li>
          ))}
        </ul>
      )}

      <Dialog open={open} onOpenChange={value => { if (!saving) setOpen(value) }}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{t('admin.machineLinks.addTitle')}</DialogTitle>
            <DialogDescription>{t('admin.machineLinks.addDescription')}</DialogDescription>
          </DialogHeader>
          <form className="space-y-2" onSubmit={event => { event.preventDefault(); void submit() }}>
            <Label htmlFor="machine-link-code">{t('admin.machineLinks.codeLabel')}</Label>
            <Textarea
              id="machine-link-code"
              value={code}
              onChange={event => setCode(event.target.value)}
              rows={4}
              spellCheck={false}
              autoComplete="off"
              placeholder="agmachine1.…"
              aria-describedby="machine-link-code-hint"
              aria-invalid={Boolean(error)}
              className="font-mono text-xs"
            />
            <p id="machine-link-code-hint" className="text-xs text-[var(--color-foreground-muted)]">{t('admin.machineLinks.codeHint')}</p>
            {error && <p role="alert" className="text-sm text-[var(--color-destructive)]">{error}</p>}
            <DialogFooter>
              <Button type="button" variant="outline" disabled={saving} onClick={() => setOpen(false)}>{t('common.cancel')}</Button>
              <Button type="submit" disabled={saving || !code.trim()}>
                {saving && <Loader2 className="animate-spin" />}
                {t('admin.machineLinks.connect')}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </section>
  )
}

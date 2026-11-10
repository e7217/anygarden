import { useId, useState, type FormEvent } from 'react'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { useLocale } from '@/i18n/LocaleProvider'
import { AgentLinkError, type AgentLink, type LinkAgent } from '@/lib/agentLinks'

export interface LinkCreateTarget { fromId?: string; toId?: string; lockFrom?: boolean }
interface Props {
  target: LinkCreateTarget | null
  onClose: () => void
  agents: LinkAgent[]
  links: AgentLink[]
  onCreate: (input: { from_agent_id: string; to_agent_id: string }) => Promise<unknown>
}
export function linkErrorMessage(t: ReturnType<typeof useLocale>['t'], error: unknown): string {
  if (error instanceof AgentLinkError) {
    if (error.status === 409) return t('agentLinks.errorDuplicate')
    if (error.status === 404) return t('agentLinks.errorUnknown')
    return error.detail ?? t('agentLinks.errorGeneric', { status: error.status })
  }
  return t('agentLinks.errorGeneric', { status: 0 })
}
export default function LinkCreateDialog({ target, onClose, ...rest }: Props) {
  return <Dialog open={target !== null} onOpenChange={open => { if (!open) onClose() }}>
    <DialogContent className="max-w-lg">
      {target && <CreateForm key={`${target.fromId ?? ''}>${target.toId ?? ''}`} target={target} onClose={onClose} {...rest} />}
    </DialogContent>
  </Dialog>
}
function CreateForm({ target, onClose, agents, links, onCreate }: Props & { target: LinkCreateTarget }) {
  const { t } = useLocale()
  const id = useId()
  const [fromId, setFromId] = useState(target.fromId ?? '')
  const [toId, setToId] = useState(target.toId ?? '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const sorted = [...agents].sort((a, b) => a.name.localeCompare(b.name))
  const unavailable = new Set(links.filter(l => l.from_agent_id === fromId).map(l => l.to_agent_id))
  async function submit(event: FormEvent) {
    event.preventDefault()
    if (saving) return
    if (!fromId || !toId) { setError(t('agentLinks.chooseBoth')); return }
    if (fromId === toId) { setError(t('agentLinks.sameAgent')); return }
    setSaving(true)
    setError(null)
    try { await onCreate({ from_agent_id: fromId, to_agent_id: toId }); onClose() }
    catch (failure) { setError(linkErrorMessage(t, failure)); setSaving(false) }
  }
  return <form onSubmit={submit} className="grid gap-4">
    <DialogHeader><DialogTitle>{t('agentLinks.createTitle')}</DialogTitle><DialogDescription>{t('agentLinks.dialogDescriptionPending')}</DialogDescription></DialogHeader>
    <div className="grid gap-3 sm:grid-cols-2">
      <div className="grid gap-1.5"><Label htmlFor={`${id}-from`}>{t('agentLinks.from')}</Label>
        <Select id={`${id}-from`} value={fromId} disabled={target.lockFrom || saving} onChange={e => { setFromId(e.target.value); setToId(''); setError(null) }}>
          <option value="" disabled>{t('agentLinks.chooseAgent')}</option>{sorted.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
        </Select>
      </div>
      <div className="grid gap-1.5"><Label htmlFor={`${id}-to`}>{t('agentLinks.to')}</Label>
        <Select id={`${id}-to`} value={toId} disabled={saving} onChange={e => { setToId(e.target.value); setError(null) }}>
          <option value="" disabled>{t('agentLinks.chooseAgent')}</option>{sorted.map(a => <option key={a.id} value={a.id} disabled={a.id === fromId || unavailable.has(a.id)}>{a.name}</option>)}
        </Select>
      </div>
    </div>
    {error && <p role="alert" className="text-caption text-[var(--color-danger)]">{error}</p>}
    <DialogFooter><Button type="button" variant="outline" onClick={onClose} disabled={saving}>{t('common.cancel')}</Button><Button type="submit" disabled={saving || unavailable.has(toId)}>{saving ? t('agentLinks.saving') : t('agentLinks.create')}</Button></DialogFooter>
  </form>
}

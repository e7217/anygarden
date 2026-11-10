import { useId, useState, type FormEvent } from 'react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { useLocale } from '@/i18n/LocaleProvider'
import { AgentLinkError, WHEN_TO_ASK_MAX, type AgentLink, type LinkAgent } from '@/lib/agentLinks'

/** What the dialog edits: a new link (ends optionally fixed) or an existing one. */
export type LinkEditorTarget =
  | { kind: 'create'; fromId?: string; toId?: string; lockFrom?: boolean; lockTo?: boolean }
  | { kind: 'edit'; link: AgentLink }

interface Props {
  target: LinkEditorTarget | null
  onClose: () => void
  agents: LinkAgent[]
  links: AgentLink[]
  onCreate: (input: { from_agent_id: string; to_agent_id: string; when_to_ask: string }) => Promise<unknown>
  onUpdate: (id: string, whenToAsk: string) => Promise<unknown>
}

type Translate = ReturnType<typeof useLocale>['t']

/** Human message for a failed link write; the server detail wins when the status is unexpected. */
export function linkErrorMessage(t: Translate, error: unknown): string {
  if (error instanceof AgentLinkError) {
    if (error.status === 409) return t('agentLinks.errorDuplicate')
    if (error.status === 404) return t('agentLinks.errorUnknown')
    if (error.status === 400 && !error.detail) return t('agentLinks.sameAgent')
    return error.detail ?? t('agentLinks.errorGeneric', { status: error.status })
  }
  return t('agentLinks.errorGeneric', { status: 0 })
}

/**
 * Create or edit one link's ``when_to_ask`` sentence (#1006). Used by
 * the relationship map (drag to connect, edge details) and by the
 * agent settings list. The form remounts per target so a reopened
 * dialog never shows a stale draft or error.
 */
export default function LinkEditorDialog({ target, onClose, ...rest }: Props) {
  return (
    <Dialog open={target !== null} onOpenChange={open => { if (!open) onClose() }}>
      <DialogContent className="max-w-lg">
        {target && (
          <LinkEditorForm
            key={target.kind === 'edit' ? target.link.id : `${target.fromId ?? ''}>${target.toId ?? ''}`}
            target={target}
            onClose={onClose}
            {...rest}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}

function LinkEditorForm({ target, onClose, agents, links, onCreate, onUpdate }: Props & { target: LinkEditorTarget }) {
  const { t } = useLocale()
  const formId = useId()
  const editing = target.kind === 'edit' ? target.link : null
  const [fromId, setFromId] = useState(editing?.from_agent_id ?? (target.kind === 'create' ? target.fromId ?? '' : ''))
  const [toId, setToId] = useState(editing?.to_agent_id ?? (target.kind === 'create' ? target.toId ?? '' : ''))
  const [text, setText] = useState(editing?.when_to_ask ?? '')
  const [touched, setTouched] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const lockFrom = editing !== null || (target.kind === 'create' && Boolean(target.lockFrom))
  const lockTo = editing !== null || (target.kind === 'create' && Boolean(target.lockTo))
  const nameOf = (id: string) => agents.find(agent => agent.id === id)?.name ?? id.slice(0, 8)
  const sorted = [...agents].sort((a, b) => a.name.localeCompare(b.name))
  // Targets the chosen source already links to are offered disabled,
  // so a duplicate is visible rather than a 409 after typing.
  const linkedTargets = new Set(links.filter(link => link.from_agent_id === fromId).map(link => link.to_agent_id))

  const trimmed = text.trim()
  const textError = trimmed.length === 0
    ? t('agentLinks.whenToAskRequired')
    : trimmed.length > WHEN_TO_ASK_MAX
      ? t('agentLinks.whenToAskTooLong', { max: WHEN_TO_ASK_MAX })
      : null
  const endsError = !fromId || !toId
    ? t('agentLinks.chooseBoth')
    : fromId === toId ? t('agentLinks.sameAgent') : null

  async function submit(event: FormEvent) {
    event.preventDefault()
    setTouched(true)
    if (textError || (!editing && endsError) || saving) return
    setSaving(true)
    setError(null)
    try {
      if (editing) await onUpdate(editing.id, trimmed)
      else await onCreate({ from_agent_id: fromId, to_agent_id: toId, when_to_ask: trimmed })
      onClose()
    } catch (failure) {
      setError(linkErrorMessage(t, failure))
      setSaving(false)
    }
  }

  const description = fromId && toId && fromId !== toId
    ? t('agentLinks.dialogDescription', { from: nameOf(fromId), to: nameOf(toId) })
    : t('agentLinks.dialogDescriptionPending')
  const textId = `${formId}-text`
  const hintId = `${formId}-hint`
  const errorId = `${formId}-error`
  const showTextError = touched && textError

  return (
    <form onSubmit={submit} className="grid gap-4" noValidate>
      <DialogHeader>
        <DialogTitle>{editing ? t('agentLinks.editTitle') : t('agentLinks.createTitle')}</DialogTitle>
        <DialogDescription>{description}</DialogDescription>
      </DialogHeader>

      {!(lockFrom && lockTo) && (
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="grid gap-1.5">
            <Label htmlFor={`${formId}-from`}>{t('agentLinks.from')}</Label>
            <Select id={`${formId}-from`} value={fromId} disabled={lockFrom} onChange={event => setFromId(event.target.value)}>
              <option value="" disabled>{t('agentLinks.chooseAgent')}</option>
              {sorted.map(agent => <option key={agent.id} value={agent.id}>{agent.name}</option>)}
            </Select>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor={`${formId}-to`}>{t('agentLinks.to')}</Label>
            <Select id={`${formId}-to`} value={toId} disabled={lockTo} onChange={event => setToId(event.target.value)}>
              <option value="" disabled>{t('agentLinks.chooseAgent')}</option>
              {sorted.map(agent => (
                <option key={agent.id} value={agent.id} disabled={agent.id === fromId || linkedTargets.has(agent.id)}>
                  {agent.name}
                </option>
              ))}
            </Select>
          </div>
        </div>
      )}

      <div className="grid gap-1.5">
        <div className="flex items-baseline justify-between gap-3">
          <Label htmlFor={textId}>{t('agentLinks.whenToAsk')}</Label>
          <span
            id={hintId}
            className={`text-badge tabular-nums ${trimmed.length > WHEN_TO_ASK_MAX ? 'text-[var(--color-danger)]' : 'text-[var(--color-foreground-muted)]'}`}
          >
            {t('agentLinks.charCount', { count: trimmed.length, max: WHEN_TO_ASK_MAX })}
          </span>
        </div>
        <Textarea
          id={textId}
          value={text}
          autoFocus
          rows={3}
          placeholder={t('agentLinks.whenToAskPlaceholder')}
          aria-invalid={showTextError ? true : undefined}
          aria-describedby={showTextError ? `${hintId} ${errorId}` : hintId}
          onChange={event => setText(event.target.value)}
          onBlur={() => setTouched(true)}
        />
        {showTextError && <p id={errorId} className="text-caption text-[var(--color-danger)]">{textError}</p>}
      </div>

      {touched && !editing && endsError && !(lockFrom && lockTo) && (
        <p className="text-caption text-[var(--color-danger)]">{endsError}</p>
      )}
      {error && <p role="alert" className="text-caption text-[var(--color-danger)]">{error}</p>}

      <DialogFooter>
        <Button type="button" variant="outline" onClick={onClose}>{t('common.cancel')}</Button>
        <Button type="submit" disabled={saving}>
          {saving ? t('agentLinks.saving') : editing ? t('common.save') : t('agentLinks.create')}
        </Button>
      </DialogFooter>
    </form>
  )
}

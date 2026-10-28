import { useEffect, useId, useState, type ReactNode } from 'react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { useLocale } from '@/i18n/LocaleProvider'
import type { CampaignCreateInput, CampaignMode } from '@/lib/campaigns'
import { campaignErrorMessage } from './campaignErrors'
import {
  ADVANCED_FIELDS,
  buildCampaignInput,
  firstInvalidField,
  campaignFormFields,
  emptyCampaignDraft,
  type CampaignDraft,
  type DraftErrors,
  type DraftField,
} from './campaignDraft'

interface CampaignFormProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSubmit: (input: CampaignCreateInput) => Promise<unknown>
  onCreated?: () => void
  /** #940 — title to pre-fill each time the form opens (``/goal title``). */
  initialTitle?: string
  /** #947 — an agent's draft to edit before creating it. */
  initialDraft?: CampaignDraft | null
}

const labelClass = 'text-caption font-medium text-[var(--color-foreground)]'
const hintClass = 'text-caption text-[var(--color-foreground-muted)]'
const errorClass = 'text-caption text-[var(--color-danger)]'
const legendClass = 'mb-2 text-body font-semibold text-[var(--color-foreground)]'

/**
 * Create-campaign dialog (#930 §3.9). The mode is chosen first because
 * it decides how the campaign ends and so which inputs show (see
 * ``campaignDraft.ts``); rarely changed evaluator settings, trusted
 * evaluation and the optional token budget (#963) stay folded under
 * "Advanced" with the server defaults.
 */
export default function CampaignForm({ open, onOpenChange, onSubmit, onCreated, initialTitle = '', initialDraft = null }: CampaignFormProps) {
  const { t } = useLocale()
  const id = useId()
  const [draft, setDraft] = useState<CampaignDraft>(emptyCampaignDraft)
  const [errors, setErrors] = useState<DraftErrors>({})
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [advancedOpen, setAdvancedOpen] = useState(false)
  // The field to focus once a failed submit has rendered its errors.
  const [focusField, setFocusField] = useState<{ field: DraftField } | null>(null)

  useEffect(() => {
    if (!open) return
    setDraft(initialDraft ?? { ...emptyCampaignDraft(), title: initialTitle })
    setErrors({})
    setSubmitError(null)
    // An agent's draft with a token budget shows it rather than hiding it.
    setAdvancedOpen(Boolean(initialDraft?.budgetTokens.trim()))
  }, [open, initialTitle, initialDraft])

  useEffect(() => {
    if (!focusField) return
    document.getElementById(`${id}-${focusField.field}`)?.focus()
  }, [focusField, id])

  const rules = campaignFormFields(draft.mode)
  const set = <K extends keyof CampaignDraft>(key: K, value: CampaignDraft[K]) =>
    setDraft(prev => ({ ...prev, [key]: value }))
  const fieldId = (field: string) => `${id}-${field}`

  const field = (name: DraftField, label: string, control: ReactNode, hint?: string) => (
    <div className="flex flex-col gap-1">
      <label htmlFor={fieldId(name)} className={labelClass}>{label}</label>
      {control}
      {hint && <p id={`${fieldId(name)}-hint`} className={hintClass}>{hint}</p>}
      {errors[name] && <p id={`${fieldId(name)}-error`} className={errorClass}>{t(errors[name]!)}</p>}
    </div>
  )
  const describedBy = (name: DraftField, hint = false) =>
    [hint ? `${fieldId(name)}-hint` : null, errors[name] ? `${fieldId(name)}-error` : null].filter(Boolean).join(' ') || undefined
  const inputProps = (name: DraftField, hint = false) => ({
    id: fieldId(name),
    'aria-invalid': errors[name] ? true : undefined,
    'aria-describedby': describedBy(name, hint),
  })

  const submit = async () => {
    const { errors: next, input } = buildCampaignInput(draft)
    setErrors(next)
    setSubmitError(null)
    if (!input) {
      // An error inside the folded "Advanced" group would be invisible.
      if (ADVANCED_FIELDS.some(name => next[name])) setAdvancedOpen(true)
      const first = firstInvalidField(next)
      if (first) setFocusField({ field: first })
      return
    }
    setSubmitting(true)
    try {
      await onSubmit(input)
      onOpenChange(false)
      onCreated?.()
    } catch (error) {
      setSubmitError(campaignErrorMessage(t, error, 'create'))
    } finally {
      setSubmitting(false)
    }
  }

  const modeOption = (mode: CampaignMode, label: string, hint: string) => (
    <label className="flex cursor-pointer items-start gap-3 rounded-[var(--radius-sm)] border border-[var(--color-border)] p-3 has-[:checked]:border-[var(--color-brand-text)] has-[:checked]:bg-[var(--color-surface-selected)]">
      <input
        type="radio"
        name={fieldId('mode')}
        value={mode}
        checked={draft.mode === mode}
        onChange={() => set('mode', mode)}
        className="mt-1 size-4 accent-[var(--color-brand)]"
      />
      <span className="flex flex-col gap-0.5">
        <span className="text-caption font-medium text-[var(--color-foreground)]">{label}</span>
        <span className={hintClass}>{hint}</span>
      </span>
    </label>
  )

  return (
    <Dialog open={open} onOpenChange={next => { if (!submitting) onOpenChange(next) }}>
      <DialogContent data-testid="campaign-form" className="max-w-xl">
        <DialogHeader>
          <DialogTitle>{t('campaigns.form.title')}</DialogTitle>
          <DialogDescription>{t('campaigns.form.description')}</DialogDescription>
        </DialogHeader>
        <form
          noValidate
          className="flex min-w-0 flex-col gap-6"
          onSubmit={event => { event.preventDefault(); void submit() }}
        >
          <fieldset className="flex flex-col gap-2">
            <legend className={legendClass}>{t('campaigns.form.modeLegend')}</legend>
            {modeOption('target', t('campaigns.form.modeTarget'), t('campaigns.form.modeTargetHint'))}
            {modeOption('optimize', t('campaigns.form.modeOptimize'), t('campaigns.form.modeOptimizeHint'))}
          </fieldset>

          <div className="flex flex-col gap-4">
            {field('title', t('campaigns.form.name'),
              <Input {...inputProps('title')} value={draft.title} onChange={e => set('title', e.target.value)} maxLength={500} />)}
            {field('purpose', t('campaigns.form.purpose'),
              <Textarea {...inputProps('purpose', true)} value={draft.purpose} onChange={e => set('purpose', e.target.value)} rows={3} />,
              t('campaigns.form.purposeHint'))}
          </div>

          <fieldset className="flex flex-col gap-4">
            <legend className={legendClass}>{t('campaigns.form.metricLegend')}</legend>
            <div className="grid gap-4 sm:grid-cols-2">
              {field('metricName', t('campaigns.form.metricName'),
                <Input {...inputProps('metricName')} value={draft.metricName} onChange={e => set('metricName', e.target.value)} maxLength={120} />)}
              <div className="flex flex-col gap-1">
                <label htmlFor={fieldId('direction')} className={labelClass}>{t('campaigns.form.direction')}</label>
                <Select id={fieldId('direction')} value={draft.direction} onChange={e => set('direction', e.target.value as CampaignDraft['direction'])}>
                  <option value="maximize">{t('campaigns.form.directionMaximize')}</option>
                  <option value="minimize">{t('campaigns.form.directionMinimize')}</option>
                </Select>
              </div>
              {rules.target !== 'hidden' && field('target', t('campaigns.form.target'),
                <Input {...inputProps('target')} inputMode="decimal" value={draft.target} onChange={e => set('target', e.target.value)} required={rules.target === 'required'} />)}
              {field('deadline',
                rules.deadline === 'optional' ? `${t('campaigns.form.deadline')} ${t('campaigns.form.optional')}` : t('campaigns.form.deadline'),
                <Input {...inputProps('deadline')} type="datetime-local" value={draft.deadline} onChange={e => set('deadline', e.target.value)} required={rules.deadline === 'required'} />)}
            </div>
          </fieldset>

          <fieldset className="flex flex-col gap-4">
            <legend className={legendClass}>{t('campaigns.form.evaluatorLegend')}</legend>
            {field('command', t('campaigns.form.command'),
              <Input {...inputProps('command', true)} className="font-mono" value={draft.command} onChange={e => set('command', e.target.value)} spellCheck={false} />,
              t('campaigns.form.commandHint'))}
            <div className="grid gap-4 sm:grid-cols-2">
              {field('scoreKey', t('campaigns.form.scoreKey'),
                <Input {...inputProps('scoreKey')} className="font-mono" value={draft.scoreKey} onChange={e => set('scoreKey', e.target.value)} spellCheck={false} />)}
            </div>
          </fieldset>

          <details
            className="group rounded-[var(--radius-sm)] border border-[var(--color-border)]"
            data-testid="campaign-form-advanced"
            open={advancedOpen}
            onToggle={event => setAdvancedOpen(event.currentTarget.open)}
          >
            <summary className="min-h-11 cursor-pointer content-center px-3 text-caption font-medium">{t('campaigns.form.advanced')}</summary>
            <div className="flex flex-col gap-4 border-t border-[var(--color-border)] p-3">
              {field('budgetTokens', `${t('campaigns.form.budget')} ${t('campaigns.form.optional')}`,
                <Input {...inputProps('budgetTokens', true)} inputMode="numeric" value={draft.budgetTokens} onChange={e => set('budgetTokens', e.target.value)} />,
                t('campaigns.form.budgetHint'))}
              <div className="grid gap-4 sm:grid-cols-2">
                {field('cwd', t('campaigns.form.cwd'),
                  <Input {...inputProps('cwd')} className="font-mono" value={draft.cwd} onChange={e => set('cwd', e.target.value)} spellCheck={false} />)}
                {field('timeoutSec', t('campaigns.form.timeout'),
                  <Input {...inputProps('timeoutSec')} inputMode="numeric" value={draft.timeoutSec} onChange={e => set('timeoutSec', e.target.value)} />)}
              </div>
              <div className="flex flex-col gap-1">
                <label htmlFor={fieldId('pinPaths')} className={labelClass}>{t('campaigns.form.pinPaths')}</label>
                <Textarea id={fieldId('pinPaths')} className="font-mono" rows={3} value={draft.pinPaths} onChange={e => set('pinPaths', e.target.value)} spellCheck={false} />
              </div>
              <label className="flex cursor-pointer items-start gap-3">
                <input
                  type="checkbox"
                  checked={draft.trustedExternal}
                  onChange={e => set('trustedExternal', e.target.checked)}
                  className="mt-1 size-4 accent-[var(--color-brand)]"
                  aria-describedby={fieldId('trusted-hint')}
                />
                <span className="flex flex-col gap-0.5">
                  <span className="text-caption font-medium text-[var(--color-foreground)]">{t('campaigns.form.trusted')}</span>
                  <span id={fieldId('trusted-hint')} className={hintClass}>{t('campaigns.form.trustedHint')}</span>
                </span>
              </label>
            </div>
          </details>

          {submitError && <p role="alert" className={errorClass}>{submitError}</p>}

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={submitting}>
              {t('common.cancel')}
            </Button>
            <Button type="submit" disabled={submitting}>
              {submitting ? t('campaigns.form.submitting') : t('campaigns.form.submit')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

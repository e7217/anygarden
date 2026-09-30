/**
 * EngineChangeDialog — #769. Switches an existing agent to another engine.
 *
 * Only the fields the new engine needs are asked for: a model (Pi also
 * needs a provider). Direct endpoints, keys and reasoning are configured
 * afterwards in the Model connection section, so they are not repeated here.
 */
import { useEffect, useState } from 'react'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import type { EngineCatalog } from '@/hooks/useAgents'
import { useLocale } from '@/i18n/LocaleProvider'

const providerPattern = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/

export interface EngineChange {
  engine: string
  provider: string | null
  model: string | null
}

export default function EngineChangeDialog({ open, onOpenChange, currentEngine, engines, fetchEngineCatalog, onSubmit }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  currentEngine: string
  engines: string[]
  fetchEngineCatalog?: (engine: string) => Promise<EngineCatalog | null>
  onSubmit: (change: EngineChange) => Promise<void>
}) {
  const { t } = useLocale()
  const choices = engines.filter(engine => engine !== currentEngine)
  const [engine, setEngine] = useState(choices[0] ?? '')
  const [provider, setProvider] = useState('')
  const [model, setModel] = useState('')
  const [catalog, setCatalog] = useState<EngineCatalog | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!open) return
    setEngine(current => (choices.includes(current) ? current : choices[0] ?? ''))
    setError('')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, currentEngine, engines.join(',')])

  useEffect(() => {
    setModel(''); setProvider(''); setCatalog(null)
    if (!engine || !fetchEngineCatalog) return
    let active = true
    fetchEngineCatalog(engine).then(next => { if (active) setCatalog(next) }).catch(() => {})
    return () => { active = false }
  }, [engine, fetchEngineCatalog])

  const isPi = engine === 'pi-cli'
  const ready = !!engine && (!isPi || (providerPattern.test(provider) && model.trim().length > 0))

  const submit = async () => {
    setBusy(true); setError('')
    try {
      await onSubmit({ engine, provider: isPi ? provider : null, model: model.trim() || null })
      onOpenChange(false)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t('admin.engineChange.failed'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={next => { if (!busy) onOpenChange(next) }}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{t('admin.engineChange.title')}</DialogTitle>
          <DialogDescription>{t('admin.engineChange.current', { engine: currentEngine })}</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="engine-change-engine">{t('admin.overview.engine')}</Label>
            <Select id="engine-change-engine" data-testid="engine-change-engine" value={engine} onChange={e => setEngine(e.target.value)} disabled={busy}>
              {choices.map(choice => <option key={choice} value={choice}>{choice}</option>)}
            </Select>
          </div>
          {isPi && (
            <div className="space-y-2">
              <Label htmlFor="engine-change-provider">{t('admin.modelConnection.providerId')}</Label>
              <Input id="engine-change-provider" data-testid="engine-change-provider" value={provider} onChange={e => setProvider(e.target.value)}
                placeholder={t('admin.machines.providerPlaceholder')} maxLength={64} disabled={busy} aria-invalid={!!provider && !providerPattern.test(provider)} />
            </div>
          )}
          <div className="space-y-2">
            <Label htmlFor="engine-change-model">{isPi ? t('admin.machines.modelRequired') : t('admin.machines.model')}</Label>
            {isPi ? (
              <>
                <Input id="engine-change-model" data-testid="engine-change-model" value={model} onChange={e => setModel(e.target.value)}
                  list="engine-change-models" maxLength={256} disabled={busy} placeholder={t('admin.machines.providerModelPlaceholder')} />
                <datalist id="engine-change-models">
                  {catalog?.models.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
                </datalist>
              </>
            ) : (
              <Select id="engine-change-model" data-testid="engine-change-model" value={model} onChange={e => setModel(e.target.value)} disabled={busy}>
                <option value="">{t('admin.machines.defaultModel', { model: catalog?.default_model || '—' })}</option>
                {catalog?.models.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
              </Select>
            )}
          </div>
          <p className="rounded-[var(--radius-md)] bg-[var(--color-surface-alt)] p-3 text-xs leading-relaxed text-[var(--color-foreground-muted)]">
            {t('admin.engineChange.notice')}
          </p>
          {error && <p role="alert" className="text-xs text-[var(--color-danger)]">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>{t('common.cancel')}</Button>
          <Button onClick={() => void submit()} disabled={!ready || busy} data-testid="engine-change-submit">
            {busy ? t('admin.engineChange.saving') : t('admin.engineChange.submit')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

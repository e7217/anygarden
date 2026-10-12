import { useCallback, useEffect, useId, useMemo, useState, type FormEvent } from 'react'
import {
  AlertOctagon, AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, CircleSlash,
  Gauge, Plus, RefreshCw, Trash2,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import { useFeedback } from '@/components/feedback/FeedbackProvider'
import { useAuth } from '@/hooks/useAuth'
import {
  BudgetRequestError, useBudgets,
  type BudgetIncident, type BudgetPolicy, type BudgetPolicyCreate, type BudgetPolicyUpdate,
  type BudgetScopeType, type BudgetWindowKind,
} from '@/hooks/useBudgets'
import { apiFetch } from '@/lib/api'
import { useLocale } from '@/i18n/LocaleProvider'
import { cn } from '@/lib/utils'

/**
 * USE-07 — admin token-budget policy screen.
 *
 * Typical path: pick what the policy applies to, set a ceiling, decide on
 * hard stop. Window and warning threshold are defaults most admins never
 * touch, so they live behind "Advanced settings" (opened automatically
 * when an existing policy already uses non-default values).
 */

interface NamedRef { id: string; name: string }

const DEFAULT_WINDOW: BudgetWindowKind = 'rolling_24h'
const DEFAULT_WARN = 80

type Health = 'inactive' | 'over' | 'near' | 'ok'

function healthOf(policy: BudgetPolicy, observed: number): Health {
  if (!policy.is_active) return 'inactive'
  if (observed >= policy.token_ceiling) return 'over'
  if (observed * 100 >= policy.token_ceiling * policy.warn_percent) return 'near'
  return 'ok'
}

async function loadNamed(path: string): Promise<NamedRef[]> {
  try {
    const resp = await apiFetch(path)
    if (!resp.ok) return []
    const rows = await resp.json()
    return Array.isArray(rows)
      ? rows.map((r: { id: string; name?: string }) => ({ id: r.id, name: r.name ?? r.id }))
      : []
  } catch {
    return []
  }
}

export default function AdminBudgets() {
  const { user } = useAuth()
  const isAdmin = Boolean(user?.is_admin)
  if (!isAdmin) return null
  return <AdminBudgetsContent />
}

function AdminBudgetsContent() {
  const { t, formatNumber, formatDate } = useLocale()
  const { confirm, notify } = useFeedback()
  const {
    policies, overview, status, error, refresh,
    createPolicy, updatePolicy, deletePolicy, resumeAgent,
  } = useBudgets()
  const [agents, setAgents] = useState<NamedRef[]>([])
  const [rooms, setRooms] = useState<NamedRef[]>([])
  const [editor, setEditor] = useState<BudgetPolicy | 'new' | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  useEffect(() => {
    void loadNamed('/api/v1/agents').then(setAgents)
    void loadNamed('/api/v1/rooms?is_dm=false').then(setRooms)
  }, [])

  const observedByPolicy = useMemo(
    () => new Map(overview.usage.map(u => [u.policy_id, u.observed_tokens])),
    [overview.usage],
  )

  const scopeLabel = useCallback((scopeType: string, scopeId: string | null) => {
    if (scopeType === 'agent') {
      const name = agents.find(a => a.id === scopeId)?.name ?? scopeId ?? ''
      return t('admin.budgets.scopeAgentNamed', { name })
    }
    if (scopeType === 'room') {
      const name = rooms.find(r => r.id === scopeId)?.name ?? scopeId ?? ''
      return t('admin.budgets.scopeRoomNamed', { name })
    }
    return t('admin.budgets.scopeGlobal')
  }, [agents, rooms, t])

  const handleDelete = useCallback(async (policy: BudgetPolicy) => {
    const scope = scopeLabel(policy.scope_type, policy.scope_id)
    const ok = await confirm({
      title: t('admin.budgets.deleteTitle'),
      description: t('admin.budgets.deleteConfirm', { scope }),
      destructive: true,
    })
    if (!ok) return
    setActionError(null)
    try {
      await deletePolicy(policy.id)
    } catch (err) {
      const statusCode = err instanceof BudgetRequestError ? err.status : 0
      setActionError(t('admin.budgets.deleteFailed', { status: statusCode }))
    }
  }, [confirm, deletePolicy, scopeLabel, t])

  const handleResume = useCallback(async (agent: { agent_id: string; name: string }) => {
    const ok = await confirm({
      title: t('admin.budgets.resumeTitle'),
      description: t('admin.budgets.resumeConfirm', { name: agent.name }),
      confirmLabel: t('admin.budgets.resume'),
    })
    if (!ok) return
    setActionError(null)
    try {
      await resumeAgent(agent.agent_id)
      notify({ message: t('admin.budgets.resumed', { name: agent.name }) })
    } catch (err) {
      const statusCode = err instanceof BudgetRequestError ? err.status : 0
      setActionError(t('admin.budgets.resumeFailed', { status: statusCode }))
    }
  }, [confirm, notify, resumeAgent, t])

  const showAttention = overview.paused_agents.length > 0 || overview.incidents.length > 0

  return (
    <div className="mx-auto w-full max-w-4xl space-y-6 px-4 py-5 sm:px-6 sm:py-6">
      <header className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <h1 className="text-heading text-[var(--color-foreground)]">{t('admin.budgets.title')}</h1>
          <p className="mt-1 text-caption font-normal text-[var(--color-foreground-muted)]">
            {t('admin.budgets.description')}
          </p>
        </div>
        <div className="flex w-full flex-wrap items-center gap-2 lg:w-auto lg:shrink-0 lg:justify-end">
          <Button variant="ghost" size="sm" onClick={() => void refresh()} disabled={status === 'loading'}>
            <RefreshCw className={cn('mr-1 h-3.5 w-3.5', status === 'loading' && 'animate-spin')} aria-hidden="true" />
            {t('common.refresh')}
          </Button>
          <Button size="sm" className="order-first w-full sm:w-auto lg:order-last" onClick={() => setEditor('new')}>
            <Plus className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
            {t('admin.budgets.newPolicy')}
          </Button>
        </div>
      </header>

      {status === 'error' && (
        <div role="alert" className="rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-[var(--color-danger-soft)] p-3 text-caption text-[var(--color-danger)]">
          {t('admin.budgets.loadFailed', { status: error?.status ?? 0 })}
        </div>
      )}
      {actionError && (
        <div role="alert" className="rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-[var(--color-danger-soft)] p-3 text-caption text-[var(--color-danger)]">
          {actionError}
        </div>
      )}

      {showAttention && (
        <section aria-labelledby="budget-attention-heading" className="space-y-2">
          <h2 id="budget-attention-heading" className="text-caption font-semibold text-[var(--color-foreground)]">
            {t('admin.budgets.attention')}
          </h2>
          <ul className="divide-y divide-[var(--color-border)] rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-surface-elevated)] shadow-whisper">
            {overview.paused_agents.map(agent => (
              <li key={`paused-${agent.agent_id}`} data-testid={`budget-paused-${agent.agent_id}`} className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
                <p className="flex min-w-0 items-start gap-2 text-caption text-[var(--color-foreground)]">
                  <CircleSlash className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-danger)]" aria-hidden="true" />
                  <span className="min-w-0 break-words">{t('admin.budgets.pausedAgent', { name: agent.name })}</span>
                </p>
                <Button
                  variant="outline"
                  size="sm"
                  aria-label={t('admin.budgets.resumeLabel', { name: agent.name })}
                  onClick={() => void handleResume(agent)}
                >
                  {t('admin.budgets.resume')}
                </Button>
              </li>
            ))}
            {overview.incidents.map(incident => (
              <IncidentRow
                key={incident.id}
                incident={incident}
                scope={scopeLabel(incident.scope_type, incident.scope_id)}
                observed={formatNumber(incident.observed_tokens)}
                time={formatDate(new Date(incident.created_at), { dateStyle: 'medium', timeStyle: 'short' })}
              />
            ))}
          </ul>
        </section>
      )}

      <section aria-labelledby="budget-policies-heading" className="space-y-2">
        <h2 id="budget-policies-heading" className="text-caption font-semibold text-[var(--color-foreground)]">
          {t('admin.budgets.policies')}
        </h2>
        {status === 'loading' && policies.length === 0 ? (
          <p role="status" className="text-caption text-[var(--color-foreground-muted)]">{t('admin.budgets.loading')}</p>
        ) : policies.length === 0 ? (
          <div className="rounded-[var(--radius-lg)] border border-dashed border-[var(--color-border)] px-6 py-10 text-center">
            <Gauge className="mx-auto mb-3 h-8 w-8 text-[var(--color-foreground-subtle)]" strokeWidth={1.5} aria-hidden="true" />
            <p className="text-caption text-[var(--color-foreground-muted)]">{t('admin.budgets.empty')}</p>
            <Button size="sm" className="mt-4" onClick={() => setEditor('new')}>
              <Plus className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
              {t('admin.budgets.newPolicy')}
            </Button>
          </div>
        ) : (
          <ul className="space-y-2">
            {policies.map(policy => (
              <PolicyRow
                key={policy.id}
                policy={policy}
                observed={observedByPolicy.get(policy.id) ?? 0}
                scope={scopeLabel(policy.scope_type, policy.scope_id)}
                onEdit={() => setEditor(policy)}
                onDelete={() => void handleDelete(policy)}
              />
            ))}
          </ul>
        )}
      </section>

      {editor && (
        <PolicyDialog
          policy={editor === 'new' ? null : editor}
          scopeText={editor === 'new' ? '' : scopeLabel(editor.scope_type, editor.scope_id)}
          agents={agents}
          rooms={rooms}
          onClose={() => setEditor(null)}
          onCreate={async body => { await createPolicy(body); setEditor(null) }}
          onUpdate={async (id, body) => { await updatePolicy(id, body); setEditor(null) }}
        />
      )}
    </div>
  )
}

function HealthBadge({ health }: { health: Health }) {
  const { t } = useLocale()
  const map = {
    inactive: { icon: CircleSlash, label: t('admin.budgets.inactive'), cls: 'bg-[var(--color-surface-alt)] text-[var(--color-foreground-muted)]' },
    over: { icon: AlertOctagon, label: t('admin.budgets.overCeiling'), cls: 'bg-[var(--color-danger-soft)] text-[var(--color-danger)]' },
    near: { icon: AlertTriangle, label: t('admin.budgets.nearCeiling'), cls: 'bg-[var(--color-warning-soft)] text-[var(--color-warning)]' },
    ok: { icon: CheckCircle2, label: t('admin.budgets.withinBudget'), cls: 'bg-[var(--color-success-soft)] text-[var(--color-success)]' },
  }[health]
  const Icon = map.icon
  return (
    <span className={cn('inline-flex items-center gap-1 rounded-[var(--radius-pill)] px-2 py-0.5 text-badge font-medium', map.cls)}>
      <Icon className="h-3.5 w-3.5" aria-hidden="true" />
      {map.label}
    </span>
  )
}

function PolicyRow({
  policy, observed, scope, onEdit, onDelete,
}: {
  policy: BudgetPolicy
  observed: number
  scope: string
  onEdit: () => void
  onDelete: () => void
}) {
  const { t, formatNumber } = useLocale()
  const health = healthOf(policy, observed)
  const percent = Math.round((observed / policy.token_ceiling) * 100)
  const fill = health === 'over'
    ? 'bg-[var(--color-danger)]'
    : health === 'near'
      ? 'bg-[var(--color-warning)]'
      : health === 'ok' ? 'bg-[var(--color-success)]' : 'bg-[var(--color-foreground-subtle)]'
  return (
    <li
      data-testid={`budget-policy-${policy.id}`}
      className="rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-4 py-3 shadow-[var(--shadow-card)]"
    >
      <div className="flex min-w-0 flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0 flex-1 space-y-2">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <h3 className="min-w-0 break-words text-caption font-semibold text-[var(--color-foreground)]">{scope}</h3>
            <HealthBadge health={health} />
            <span className="inline-flex items-center rounded-[var(--radius-pill)] border border-[var(--color-border)] px-2 py-0.5 text-badge text-[var(--color-foreground)]">
              {policy.hard_stop_enabled ? t('admin.budgets.hardStopOn') : t('admin.budgets.hardStopOff')}
            </span>
          </div>
          <div>
            <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2 text-caption">
              <span className="text-[var(--color-foreground)]">
                {t('admin.budgets.usageOf', { observed: formatNumber(observed), ceiling: formatNumber(policy.token_ceiling) })}
              </span>
              <span className="text-[var(--color-foreground-muted)]">
                {t('admin.budgets.usagePercent', { percent })} · {policy.window_kind === 'calendar_day_utc' ? t('admin.budgets.windowCalendar') : t('admin.budgets.windowRolling')}
              </span>
            </div>
            <div
              role="progressbar"
              aria-label={t('admin.budgets.usageLabel', { scope })}
              aria-valuemin={0}
              aria-valuemax={policy.token_ceiling}
              aria-valuenow={Math.min(observed, policy.token_ceiling)}
              aria-valuetext={t('admin.budgets.usagePercent', { percent })}
              className="relative h-2 overflow-hidden rounded-[var(--radius-sm)] bg-[var(--color-surface-alt)]"
            >
              <div className={cn('h-full rounded-[var(--radius-sm)]', fill)} style={{ width: `${Math.min(percent, 100)}%` }} />
            </div>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-1 border-t border-[var(--color-border)] pt-2 lg:border-t-0 lg:pt-0">
          <Button variant="ghost" size="sm" onClick={onEdit} aria-label={t('admin.budgets.editLabel', { scope })}>
            {t('admin.budgets.edit')}
          </Button>
          <Button variant="ghost" size="icon" onClick={onDelete} aria-label={t('admin.budgets.deleteLabel', { scope })}>
            <Trash2 className="h-4 w-4 text-[var(--color-danger)]" aria-hidden="true" />
          </Button>
        </div>
      </div>
    </li>
  )
}

function IncidentRow({
  incident, scope, observed, time,
}: { incident: BudgetIncident; scope: string; observed: string; time: string }) {
  const { t } = useLocale()
  const hard = incident.threshold_type === 'hard'
  const Icon = hard ? AlertOctagon : AlertTriangle
  return (
    <li data-testid={`budget-incident-${incident.id}`} className="flex items-start gap-2 px-4 py-3">
      <Icon className={cn('mt-0.5 h-4 w-4 shrink-0', hard ? 'text-[var(--color-danger)]' : 'text-[var(--color-warning)]')} aria-hidden="true" />
      <div className="min-w-0">
        <p className="text-caption font-medium text-[var(--color-foreground)]">
          {hard ? t('admin.budgets.incidentHard') : t('admin.budgets.incidentSoft')}
        </p>
        <p className="break-words text-caption text-[var(--color-foreground-muted)]">
          {t('admin.budgets.incidentDetail', { scope, observed, time })}
        </p>
      </div>
    </li>
  )
}

// ── Create / edit dialog ────────────────────────────────────────────

interface PolicyDialogProps {
  policy: BudgetPolicy | null
  scopeText: string
  agents: NamedRef[]
  rooms: NamedRef[]
  onClose: () => void
  onCreate: (body: BudgetPolicyCreate) => Promise<void>
  onUpdate: (id: string, body: BudgetPolicyUpdate) => Promise<void>
}

function PolicyDialog({ policy, scopeText, agents, rooms, onClose, onCreate, onUpdate }: PolicyDialogProps) {
  const { t } = useLocale()
  const uid = useId()
  const isCreate = policy === null
  const [scopeType, setScopeType] = useState<BudgetScopeType>(policy?.scope_type ?? 'global')
  const [scopeId, setScopeId] = useState(policy?.scope_id ?? '')
  const [ceiling, setCeiling] = useState(policy ? String(policy.token_ceiling) : '')
  const [hardStop, setHardStop] = useState(policy?.hard_stop_enabled ?? false)
  const [active, setActive] = useState(policy?.is_active ?? true)
  const [windowKind, setWindowKind] = useState<BudgetWindowKind>(policy?.window_kind ?? DEFAULT_WINDOW)
  const [warn, setWarn] = useState(String(policy?.warn_percent ?? DEFAULT_WARN))
  const [advancedOpen, setAdvancedOpen] = useState(
    Boolean(policy && (policy.window_kind !== DEFAULT_WINDOW || policy.warn_percent !== DEFAULT_WARN)),
  )
  const [submitted, setSubmitted] = useState(false)
  const [busy, setBusy] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  const ceilingValue = Number(ceiling)
  const warnValue = Number(warn)
  const ceilingInvalid = !/^\d+$/.test(ceiling.trim()) || ceilingValue < 1
  const warnInvalid = !/^\d+$/.test(warn.trim()) || warnValue < 1 || warnValue > 100
  const targetInvalid = isCreate && scopeType !== 'global' && !scopeId
  const targets = scopeType === 'agent' ? agents : rooms

  const ids = {
    scope: `${uid}-scope`, target: `${uid}-target`, targetError: `${uid}-target-error`,
    ceiling: `${uid}-ceiling`, ceilingHint: `${uid}-ceiling-hint`, ceilingError: `${uid}-ceiling-error`,
    hardStop: `${uid}-hard-stop`, hardStopHint: `${uid}-hard-stop-hint`,
    active: `${uid}-active`, activeHint: `${uid}-active-hint`,
    advanced: `${uid}-advanced`, window: `${uid}-window`,
    warn: `${uid}-warn`, warnHint: `${uid}-warn-hint`, warnError: `${uid}-warn-error`,
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setSubmitted(true)
    if (ceilingInvalid || targetInvalid) return
    if (warnInvalid) { setAdvancedOpen(true); return }
    setBusy(true)
    setSaveError(null)
    try {
      if (isCreate) {
        await onCreate({
          scope_type: scopeType,
          scope_id: scopeType === 'global' ? null : scopeId,
          token_ceiling: ceilingValue,
          warn_percent: warnValue,
          window_kind: windowKind,
          hard_stop_enabled: hardStop,
        })
      } else {
        const changes: BudgetPolicyUpdate = {}
        if (ceilingValue !== policy.token_ceiling) changes.token_ceiling = ceilingValue
        if (warnValue !== policy.warn_percent) changes.warn_percent = warnValue
        if (windowKind !== policy.window_kind) changes.window_kind = windowKind
        if (hardStop !== policy.hard_stop_enabled) changes.hard_stop_enabled = hardStop
        if (active !== policy.is_active) changes.is_active = active
        if (Object.keys(changes).length === 0) { onClose(); return }
        await onUpdate(policy.id, changes)
      }
    } catch (err) {
      const statusCode = err instanceof BudgetRequestError ? err.status : 0
      const detail = err instanceof BudgetRequestError && err.message ? ` ${err.message}` : ''
      setSaveError(`${t('admin.budgets.saveFailed', { status: statusCode })}${detail}`)
    } finally {
      setBusy(false)
    }
  }

  const errorText = 'text-caption text-[var(--color-danger)]'
  const hintText = 'text-caption text-[var(--color-foreground-muted)]'

  return (
    <Dialog open onOpenChange={open => { if (!open) onClose() }}>
      <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{isCreate ? t('admin.budgets.createTitle') : t('admin.budgets.editTitle')}</DialogTitle>
          <DialogDescription>{t('admin.budgets.formDescription')}</DialogDescription>
        </DialogHeader>
        <form noValidate onSubmit={submit} className="space-y-4">
          {isCreate ? (
            <div className="space-y-1.5">
              <Label htmlFor={ids.scope}>{t('admin.budgets.scope')}</Label>
              <Select
                id={ids.scope}
                value={scopeType}
                onChange={e => { setScopeType(e.target.value as BudgetScopeType); setScopeId('') }}
              >
                <option value="global">{t('admin.budgets.scopeGlobal')}</option>
                <option value="agent">{t('admin.budgets.scopeAgent')}</option>
                <option value="room">{t('admin.budgets.scopeRoom')}</option>
              </Select>
            </div>
          ) : (
            <div className="space-y-1.5">
              <p className="text-sm font-semibold text-[var(--color-foreground)]">{t('admin.budgets.scope')}</p>
              <p className="text-caption text-[var(--color-foreground)]">{scopeText}</p>
            </div>
          )}

          {isCreate && scopeType !== 'global' && (
            <div className="space-y-1.5">
              <Label htmlFor={ids.target}>{scopeType === 'agent' ? t('admin.budgets.agent') : t('admin.budgets.room')}</Label>
              {targets.length === 0 ? (
                <p id={ids.target} className={hintText}>
                  {scopeType === 'agent' ? t('admin.budgets.noAgents') : t('admin.budgets.noRooms')}
                </p>
              ) : (
                <Select
                  id={ids.target}
                  value={scopeId}
                  onChange={e => setScopeId(e.target.value)}
                  aria-invalid={submitted && targetInvalid}
                  aria-describedby={submitted && targetInvalid ? ids.targetError : undefined}
                >
                  <option value="">{scopeType === 'agent' ? t('admin.budgets.chooseAgent') : t('admin.budgets.chooseRoom')}</option>
                  {targets.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
                </Select>
              )}
              {submitted && targetInvalid && (
                <p id={ids.targetError} role="alert" className={errorText}>{t('admin.budgets.targetRequired')}</p>
              )}
            </div>
          )}

          <div className="space-y-1.5">
            <Label htmlFor={ids.ceiling}>{t('admin.budgets.ceiling')}</Label>
            <Input
              id={ids.ceiling}
              type="number"
              inputMode="numeric"
              min={1}
              step={1}
              value={ceiling}
              onChange={e => setCeiling(e.target.value)}
              aria-invalid={submitted && ceilingInvalid}
              aria-describedby={[ids.ceilingHint, submitted && ceilingInvalid ? ids.ceilingError : ''].filter(Boolean).join(' ')}
            />
            <p id={ids.ceilingHint} className={hintText}>{t('admin.budgets.ceilingHint')}</p>
            {submitted && ceilingInvalid && (
              <p id={ids.ceilingError} role="alert" className={errorText}>{t('admin.budgets.ceilingInvalid')}</p>
            )}
          </div>

          <div className="flex items-start gap-3">
            <input
              id={ids.hardStop}
              type="checkbox"
              checked={hardStop}
              onChange={e => setHardStop(e.target.checked)}
              aria-describedby={ids.hardStopHint}
              className="mt-0.5 size-4 shrink-0 accent-[var(--color-brand)]"
            />
            <div className="min-w-0 space-y-1">
              <Label htmlFor={ids.hardStop}>{t('admin.budgets.hardStop')}</Label>
              <p id={ids.hardStopHint} className={hintText}>{t('admin.budgets.hardStopHint')}</p>
            </div>
          </div>

          {!isCreate && (
            <div className="flex items-start gap-3">
              <input
                id={ids.active}
                type="checkbox"
                checked={active}
                onChange={e => setActive(e.target.checked)}
                aria-describedby={ids.activeHint}
                className="mt-0.5 size-4 shrink-0 accent-[var(--color-brand)]"
              />
              <div className="min-w-0 space-y-1">
                <Label htmlFor={ids.active}>{t('admin.budgets.active')}</Label>
                <p id={ids.activeHint} className={hintText}>{t('admin.budgets.activeHint')}</p>
              </div>
            </div>
          )}

          <div className="border-t border-[var(--color-border)] pt-3">
            <button
              type="button"
              aria-expanded={advancedOpen}
              aria-controls={ids.advanced}
              onClick={() => setAdvancedOpen(open => !open)}
              className="flex min-h-[var(--control-sm-height)] items-center gap-1 rounded-[var(--radius-sm)] text-sm font-medium text-[var(--color-brand-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-brand-focus)]"
            >
              {advancedOpen
                ? <ChevronDown className="h-4 w-4" aria-hidden="true" />
                : <ChevronRight className="h-4 w-4" aria-hidden="true" />}
              {t('admin.budgets.advanced')}
            </button>
            {advancedOpen && (
              <div id={ids.advanced} className="mt-3 space-y-4">
                <div className="space-y-1.5">
                  <Label htmlFor={ids.window}>{t('admin.budgets.window')}</Label>
                  <Select id={ids.window} value={windowKind} onChange={e => setWindowKind(e.target.value as BudgetWindowKind)}>
                    <option value="rolling_24h">{t('admin.budgets.windowRolling')}</option>
                    <option value="calendar_day_utc">{t('admin.budgets.windowCalendar')}</option>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor={ids.warn}>{t('admin.budgets.warnPercent')}</Label>
                  <Input
                    id={ids.warn}
                    type="number"
                    inputMode="numeric"
                    min={1}
                    max={100}
                    step={1}
                    value={warn}
                    onChange={e => setWarn(e.target.value)}
                    aria-invalid={submitted && warnInvalid}
                    aria-describedby={[ids.warnHint, submitted && warnInvalid ? ids.warnError : ''].filter(Boolean).join(' ')}
                  />
                  <p id={ids.warnHint} className={hintText}>{t('admin.budgets.warnPercentHint')}</p>
                  {submitted && warnInvalid && (
                    <p id={ids.warnError} role="alert" className={errorText}>{t('admin.budgets.warnPercentInvalid')}</p>
                  )}
                </div>
              </div>
            )}
          </div>

          {saveError && <p role="alert" className={errorText}>{saveError}</p>}

          <DialogFooter>
            <Button type="button" variant="ghost" onClick={onClose} disabled={busy}>{t('common.cancel')}</Button>
            <Button type="submit" disabled={busy}>
              {busy ? t('admin.budgets.saving') : isCreate ? t('admin.budgets.create') : t('common.save')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

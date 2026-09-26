import { useState, useMemo } from 'react'
import { Button } from '@/components/ui/button'
import {
  createGoal,
  type Goal,
  type GoalCreateInput,
  type GoalMaterialize,
  type GoalTriggerType,
} from '@/lib/goals'
import { useLocale } from '@/i18n/LocaleProvider'

/** #312 — minimal shape needed to render the Agent picker. We only
 *  need ``id`` for the value and ``name`` for the label; any
 *  ``Agent`` from ``useAgents()`` satisfies this. Keeping the type
 *  narrow lets callers in the right rail pass a derived list (room
 *  agent participants) without resolving full ``Agent`` rows. */
export interface GoalFormAgentOption {
  id: string
  name: string
}

interface GoalFormProps {
  /** #312 — explicit candidate list. Always required so the form is
   *  forced to render an Agent picker; callers that only have one
   *  candidate (AgentSettingsDialog single-agent context) pass a
   *  one-element array. The form renders a select but disables it
   *  in the single-candidate case so the UI is informative without
   *  asking for a redundant click. */
  roomAgents: GoalFormAgentOption[]
  /** Pre-selected agent id. Defaults to the first ``roomAgents``
   *  entry if omitted — matches the implicit "first-agent" behaviour
   *  pre-#312, but the field is now visible and editable. */
  defaultAgentId?: string | null
  /** Pre-fill the report room (current room when launched from the
   *  right rail). User can change. */
  defaultReportRoomId?: string | null
  onCreated: (goal: Goal) => void
  onCancel: () => void
}

/**
 * Compact create-goal form (#302). MVP scope:
 * - cron / interval / manual trigger picker
 * - spec textarea
 * - materialize radio (interesting_only default)
 * - report_room input (pre-filled from caller, plain UUID for now —
 *   a room picker can come later)
 *
 * Server validates trigger config + agent room membership; we surface
 * the 422 ``detail`` message inline.
 */
export default function GoalForm({
  roomAgents,
  defaultAgentId = null,
  defaultReportRoomId = null,
  onCreated,
  onCancel,
}: GoalFormProps) {
  const { t } = useLocale()
  const [title, setTitle] = useState('')
  const [spec, setSpec] = useState('')
  // #312 — explicit assignee field. Defaults to ``defaultAgentId`` if
  // the caller pre-picked one (e.g. AgentSettingsDialog has a fixed
  // agent); otherwise the first candidate so the implicit pre-#312
  // behaviour is preserved when ``roomAgents.length >= 1``. Empty
  // ``roomAgents`` is a degenerate state — the caller should not
  // open this form when no agents are available, but we render the
  // disabled select rather than crashing.
  const [assigneeAgentId, setAssigneeAgentId] = useState<string>(
    defaultAgentId ?? roomAgents[0]?.id ?? '',
  )
  const [reportRoomId, setReportRoomId] = useState<string>(
    defaultReportRoomId ?? '',
  )
  const [triggerType, setTriggerType] = useState<GoalTriggerType>('cron')
  const [cronExpr, setCronExpr] = useState('0 9 * * *')
  const [intervalSecs, setIntervalSecs] = useState<number>(600)
  const [materialize, setMaterialize] =
    useState<GoalMaterialize>('interesting_only')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const triggerConfig = useMemo<Record<string, unknown>>(() => {
    if (triggerType === 'cron') return { cron: cronExpr.trim() }
    if (triggerType === 'interval')
      return { interval_seconds: Number(intervalSecs) }
    return {}
  }, [triggerType, cronExpr, intervalSecs])

  const submit = async () => {
    setError(null)
    if (!title.trim() || !spec.trim()) {
      setError(t('goals.required'))
      return
    }
    if (!assigneeAgentId) {
      // Goals require a non-null assignee at the schema level
      // (``agent_goals.assignee_agent_id`` is NOT NULL). Catch the
      // empty case here so the error is actionable rather than a
      // server 422.
      setError(t('goals.agentRequired'))
      return
    }
    setSubmitting(true)
    try {
      const input: GoalCreateInput = {
        title: title.trim(),
        spec: spec.trim(),
        trigger_type: triggerType,
        trigger_config: triggerConfig,
        materialize,
        report_room_id: reportRoomId.trim() || null,
      }
      const goal = await createGoal(assigneeAgentId, input)
      onCreated(goal)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="flex flex-col gap-3 p-3">
      <div className="flex flex-col gap-1">
        <label htmlFor="goal-title" className="text-xs font-medium text-[var(--color-foreground-muted)]">
          {t('goals.title')}
        </label>
        <input
          id="goal-title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder={t('goals.titlePlaceholder')}
          className="min-h-10 rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-foreground)]"
        />
      </div>

      <div className="flex flex-col gap-1">
        <label htmlFor="goal-assignee" className="text-xs font-medium text-[var(--color-foreground-muted)]">
          {t('goals.agent')}
        </label>
        <select
          id="goal-assignee"
          value={assigneeAgentId}
          onChange={(e) => setAssigneeAgentId(e.target.value)}
          disabled={roomAgents.length <= 1}
          aria-label={t('goals.pickAgent')}
          aria-required="true"
          data-testid="goal-form-assignee-select"
          className="min-h-10 rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-foreground)] disabled:opacity-70"
        >
          {roomAgents.length === 0 && (
            <option value="" disabled>
              {t('goals.noAgents')}
            </option>
          )}
          {roomAgents.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </select>
        {roomAgents.length === 1 && (
          <p className="text-xs text-[var(--color-foreground-muted)]">
            {t('goals.singleAgent')}
          </p>
        )}
      </div>

      <div className="flex flex-col gap-1">
        <label htmlFor="goal-spec" className="text-xs font-medium text-[var(--color-foreground-muted)]">
          {t('goals.instructions')}
        </label>
        <textarea
          id="goal-spec"
          value={spec}
          onChange={(e) => setSpec(e.target.value)}
          placeholder={t('goals.instructionsPlaceholder')}
          rows={4}
          className="rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-foreground)]"
        />
      </div>

      <fieldset className="flex flex-col gap-1">
        <legend className="text-xs font-medium text-[var(--color-foreground-muted)]">
          {t('goals.schedule')}
        </legend>
        <div className="flex flex-wrap gap-3 text-sm">
          {(['cron', 'interval', 'manual'] as const).map((choice) => (
            <label key={choice} className="flex min-h-10 items-center gap-2">
              <input
                type="radio"
                name="trigger"
                value={choice}
                checked={triggerType === choice}
                onChange={() => setTriggerType(choice)}
              />
              {t(choice === 'cron' ? 'goals.cron' : choice === 'interval' ? 'goals.interval' : 'goals.manual')}
            </label>
          ))}
        </div>
        {triggerType === 'cron' && (
          <input
            aria-label={t('goals.cron')}
            value={cronExpr}
            onChange={(e) => setCronExpr(e.target.value)}
            placeholder="0 9 * * *"
            className="mt-1 min-h-10 rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-foreground)] font-mono"
          />
        )}
        {triggerType === 'interval' && (
          <input
            aria-label={t('goals.interval')}
            type="number"
            value={intervalSecs}
            onChange={(e) => setIntervalSecs(Number(e.target.value))}
            min={60}
            placeholder="600"
            className="mt-1 min-h-10 rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-foreground)] font-mono"
          />
        )}
      </fieldset>

      <details className="rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-surface-alt)] p-3">
        <summary className="cursor-pointer text-sm font-medium text-[var(--color-foreground)]">{t('goals.advanced')}</summary>
        <div className="mt-3 flex flex-col gap-3">
          <div className="flex flex-col gap-1">
            <label htmlFor="goal-report-room" className="text-xs font-medium text-[var(--color-foreground-muted)]">{t('goals.reportRoom')}</label>
            <input
              id="goal-report-room"
              value={reportRoomId}
              onChange={(e) => setReportRoomId(e.target.value)}
              placeholder={t('goals.reportRoomPlaceholder')}
              className="min-h-10 rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-foreground)] font-mono"
            />
            <p className="text-xs text-[var(--color-foreground-muted)]">{t('goals.reportRoomHelp')}</p>
          </div>
          <fieldset className="flex flex-col gap-1">
            <legend className="text-xs font-medium text-[var(--color-foreground-muted)]">{t('goals.recording')}</legend>
            <label className="flex min-h-10 items-start gap-2 py-1 text-sm">
              <input type="radio" name="materialize" value="interesting_only" checked={materialize === 'interesting_only'} onChange={() => setMaterialize('interesting_only')} />
              <span><span className="block font-medium">{t('goals.interestingOnly')}</span><span className="text-xs text-[var(--color-foreground-muted)]">{t('goals.interestingOnlyDescription')}</span></span>
            </label>
            <label className="flex min-h-10 items-start gap-2 py-1 text-sm">
              <input type="radio" name="materialize" value="full" checked={materialize === 'full'} onChange={() => setMaterialize('full')} />
              <span><span className="block font-medium">{t('goals.full')}</span><span className="text-xs text-[var(--color-foreground-muted)]">{t('goals.fullDescription')}</span></span>
            </label>
          </fieldset>
        </div>
      </details>

      {error && (
        <p role="alert" className="text-[12px] text-[var(--color-destructive)]">
          {error}
        </p>
      )}

      <div className="flex justify-end gap-2 border-t border-[var(--color-border)] pt-2">
        <Button variant="ghost" size="sm" onClick={onCancel} disabled={submitting}>
          {t('common.cancel')}
        </Button>
        <Button size="sm" onClick={submit} disabled={submitting}>
          {submitting ? t('goals.saving') : t('goals.add')}
        </Button>
      </div>
    </div>
  )
}

import { useState, useMemo } from 'react'
import { Button } from '@/components/ui/button'
import {
  createGoal,
  type Goal,
  type GoalCreateInput,
  type GoalMaterialize,
  type GoalTriggerType,
} from '@/lib/goals'

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
      setError('제목과 spec은 필수입니다.')
      return
    }
    if (!assigneeAgentId) {
      // Goals require a non-null assignee at the schema level
      // (``agent_goals.assignee_agent_id`` is NOT NULL). Catch the
      // empty case here so the error is actionable rather than a
      // server 422.
      setError('Agent 를 선택해 주세요.')
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
        <label className="text-[11px] uppercase tracking-wider text-[var(--color-foreground-subtle)]">
          제목
        </label>
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="예: 매일 호스트 리소스 점검"
          className="rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-white px-2 py-1 text-sm"
        />
      </div>

      <div className="flex flex-col gap-1">
        <label className="text-[11px] uppercase tracking-wider text-[var(--color-foreground-subtle)]">
          Agent
        </label>
        <select
          value={assigneeAgentId}
          onChange={(e) => setAssigneeAgentId(e.target.value)}
          disabled={roomAgents.length <= 1}
          aria-label="Pick assignee agent"
          aria-required="true"
          data-testid="goal-form-assignee-select"
          className="rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-white px-2 py-1 text-sm disabled:opacity-70"
        >
          {roomAgents.length === 0 && (
            <option value="" disabled>
              (no agents available)
            </option>
          )}
          {roomAgents.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </select>
        {roomAgents.length === 1 && (
          <p className="text-[10px] text-[var(--color-foreground-subtle)]">
            룸에 에이전트가 한 명이라 자동 선택됩니다.
          </p>
        )}
      </div>

      <div className="flex flex-col gap-1">
        <label className="text-[11px] uppercase tracking-wider text-[var(--color-foreground-subtle)]">
          Spec (markdown)
        </label>
        <textarea
          value={spec}
          onChange={(e) => setSpec(e.target.value)}
          placeholder="에이전트가 매 트리거마다 받을 지시문…"
          rows={4}
          className="rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-white px-2 py-1 text-sm font-mono"
        />
      </div>

      <div className="flex flex-col gap-1">
        <label className="text-[11px] uppercase tracking-wider text-[var(--color-foreground-subtle)]">
          보고 룸 (Room ID)
        </label>
        <input
          value={reportRoomId}
          onChange={(e) => setReportRoomId(e.target.value)}
          placeholder="비우면 silent goal"
          className="rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-white px-2 py-1 text-sm font-mono"
        />
        <p className="text-[10px] text-[var(--color-foreground-subtle)]">
          에이전트는 이 룸의 참여자여야 합니다.
        </p>
      </div>

      <fieldset className="flex flex-col gap-1">
        <legend className="text-[11px] uppercase tracking-wider text-[var(--color-foreground-subtle)]">
          트리거
        </legend>
        <div className="flex gap-3 text-sm">
          {(['cron', 'interval', 'manual'] as const).map((t) => (
            <label key={t} className="flex items-center gap-1">
              <input
                type="radio"
                name="trigger"
                value={t}
                checked={triggerType === t}
                onChange={() => setTriggerType(t)}
              />
              {t}
            </label>
          ))}
        </div>
        {triggerType === 'cron' && (
          <input
            value={cronExpr}
            onChange={(e) => setCronExpr(e.target.value)}
            placeholder="0 9 * * *"
            className="mt-1 rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-white px-2 py-1 text-sm font-mono"
          />
        )}
        {triggerType === 'interval' && (
          <input
            type="number"
            value={intervalSecs}
            onChange={(e) => setIntervalSecs(Number(e.target.value))}
            min={60}
            placeholder="600"
            className="mt-1 rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-white px-2 py-1 text-sm font-mono"
          />
        )}
      </fieldset>

      <fieldset className="flex flex-col gap-1">
        <legend className="text-[11px] uppercase tracking-wider text-[var(--color-foreground-subtle)]">
          기록 정책 (materialize)
        </legend>
        <div className="flex flex-col gap-1 text-sm">
          <label className="flex items-start gap-2">
            <input
              type="radio"
              name="materialize"
              value="interesting_only"
              checked={materialize === 'interesting_only'}
              onChange={() => setMaterialize('interesting_only')}
            />
            <span>
              <span className="font-medium">interesting_only</span>
              <span className="ml-1 text-[12px] text-[var(--color-foreground-subtle)]">
                실패/주목할 결과만 Task로 남김 (조용)
              </span>
            </span>
          </label>
          <label className="flex items-start gap-2">
            <input
              type="radio"
              name="materialize"
              value="full"
              checked={materialize === 'full'}
              onChange={() => setMaterialize('full')}
            />
            <span>
              <span className="font-medium">full</span>
              <span className="ml-1 text-[12px] text-[var(--color-foreground-subtle)]">
                매 실행을 Task로 남김 (자세)
              </span>
            </span>
          </label>
        </div>
      </fieldset>

      {error && (
        <p role="alert" className="text-[12px] text-[var(--color-destructive)]">
          {error}
        </p>
      )}

      <div className="flex justify-end gap-2 border-t border-[var(--color-border)] pt-2">
        <Button variant="ghost" size="sm" onClick={onCancel} disabled={submitting}>
          취소
        </Button>
        <Button size="sm" onClick={submit} disabled={submitting}>
          {submitting ? '저장 중…' : '책임 추가'}
        </Button>
      </div>
    </div>
  )
}

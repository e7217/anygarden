import { useState, useMemo } from 'react'
import { Button } from '@/components/ui/button'
import {
  createGoal,
  type Goal,
  type GoalCreateInput,
  type GoalMaterialize,
  type GoalTriggerType,
} from '@/lib/goals'

interface GoalFormProps {
  agentId: string
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
  agentId,
  defaultReportRoomId = null,
  onCreated,
  onCancel,
}: GoalFormProps) {
  const [title, setTitle] = useState('')
  const [spec, setSpec] = useState('')
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
      const goal = await createGoal(agentId, input)
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
        <p role="alert" className="text-[12px] text-red-600">
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

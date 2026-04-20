import { useEffect, useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { Plus, Edit, Trash2, Zap, CheckCircle2, Circle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  useGatewayModels,
  type GatewayModel,
  type TestResult,
} from '@/hooks/useLLMGateway'
import type { LLMGatewayOutletContext } from '@/pages/AdminLLMGatewayPage'
import { ModelDialog } from './ModelDialog'
import { cn } from '@/lib/utils'

/**
 * Card-list of registered gateway models. Each card carries the
 * per-model test / edit / delete controls; the top-right Add button
 * opens the shared ModelDialog.
 */

export function ModelsSection() {
  const { models, status, error, refresh, create, update, remove, test } =
    useGatewayModels()
  const { incrementPending, applyBump } =
    useOutletContext<LLMGatewayOutletContext>()

  // Editing state. ``null`` = closed; ``'new'`` = create dialog; a
  // ``GatewayModel`` = edit mode prefilled with that row.
  const [dialog, setDialog] = useState<GatewayModel | 'new' | null>(null)

  // Per-card test result (keyed by model id). ``null`` = in-flight.
  const [testResults, setTestResults] = useState<
    Record<string, TestResult | null>
  >({})

  // Refresh after Apply so the view is consistent with whatever the
  // running subprocess just picked up.
  useEffect(() => {
    if (applyBump > 0) refresh()
  }, [applyBump, refresh])

  const handleTest = async (id: string) => {
    setTestResults(prev => ({ ...prev, [id]: null }))
    try {
      const result = await test(id)
      setTestResults(prev => ({ ...prev, [id]: result }))
    } catch (err) {
      setTestResults(prev => ({
        ...prev,
        [id]: {
          ok: false,
          status_code: null,
          duration_ms: 0,
          error: err instanceof Error ? err.message : String(err),
        },
      }))
    }
  }

  const handleToggleEnabled = async (m: GatewayModel) => {
    try {
      await update(m.id, { enabled: !m.enabled })
      incrementPending()
    } catch (err) {
      console.error('[llm-gateway] toggle failed', err)
    }
  }

  const handleDelete = async (m: GatewayModel) => {
    if (!window.confirm(`Delete model "${m.model_name}"?`)) return
    try {
      await remove(m.id)
      incrementPending()
    } catch (err) {
      alert(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <div className="mx-auto max-w-3xl px-6 py-6">
      <header className="mb-6 flex items-start justify-between">
        <div>
          <h1 className="text-[22px] font-bold tracking-tight text-[var(--color-foreground)]">
            Models
          </h1>
          <p className="mt-1 text-[13px] text-[var(--color-foreground-muted)]">
            Models registered in the gateway. Agents select one by <code className="text-[12px]">model_name</code>.
          </p>
        </div>
        <Button onClick={() => setDialog('new')} size="sm">
          <Plus className="mr-1 h-3.5 w-3.5" />
          Add model
        </Button>
      </header>

      {status === 'loading' && models.length === 0 && (
        <p className="text-[13px] text-[var(--color-foreground-muted)]">Loading models…</p>
      )}

      {status === 'error' && (
        <div className="rounded-[var(--radius-md)] border border-red-200 bg-red-50 px-3 py-2 text-[13px] text-red-900">
          Couldn't load models: {error}
        </div>
      )}

      {status === 'loaded' && models.length === 0 && (
        <div className="rounded-[var(--radius-md)] border border-dashed border-[var(--color-border)] px-6 py-12 text-center">
          <p className="text-[14px] font-medium text-[var(--color-foreground)]">
            No models registered.
          </p>
          <p className="mt-1 text-[13px] text-[var(--color-foreground-muted)]">
            Add one to start routing agent traffic.
          </p>
          <Button onClick={() => setDialog('new')} className="mt-4" size="sm">
            <Plus className="mr-1 h-3.5 w-3.5" />
            Add your first model
          </Button>
        </div>
      )}

      <div className="flex flex-col gap-3">
        {models.map(m => (
          <ModelCard
            key={m.id}
            model={m}
            testResult={testResults[m.id]}
            testing={m.id in testResults && testResults[m.id] === null}
            onTest={() => handleTest(m.id)}
            onEdit={() => setDialog(m)}
            onDelete={() => handleDelete(m)}
            onToggleEnabled={() => handleToggleEnabled(m)}
          />
        ))}
      </div>

      {dialog && (
        <ModelDialog
          initial={dialog === 'new' ? null : dialog}
          onClose={() => setDialog(null)}
          onSubmit={async input => {
            if (dialog === 'new') {
              await create(input)
            } else {
              await update(dialog.id, input)
            }
            incrementPending()
            setDialog(null)
          }}
        />
      )}
    </div>
  )
}

interface CardProps {
  model: GatewayModel
  testResult: TestResult | null | undefined
  testing: boolean
  onTest: () => void
  onEdit: () => void
  onDelete: () => void
  onToggleEnabled: () => void
}

function ModelCard({
  model, testResult, testing, onTest, onEdit, onDelete, onToggleEnabled,
}: CardProps) {
  return (
    <div
      className={cn(
        'rounded-[var(--radius-md)] border border-[var(--color-border)] bg-white p-4 shadow-whisper',
        !model.enabled && 'opacity-60'
      )}
    >
      <div className="mb-1 flex items-center gap-2">
        <button
          onClick={onToggleEnabled}
          aria-label={model.enabled ? 'Disable' : 'Enable'}
          title={model.enabled ? 'Enabled — click to disable' : 'Disabled — click to enable'}
          className="text-[var(--color-foreground-subtle)] hover:text-[var(--color-foreground)]"
        >
          {model.enabled ? (
            <CheckCircle2 className="h-4 w-4 text-emerald-500" />
          ) : (
            <Circle className="h-4 w-4" />
          )}
        </button>
        <span className="font-semibold text-[var(--color-foreground)]">
          {model.model_name}
        </span>
        {!model.enabled && (
          <span className="rounded-[var(--radius-sm)] bg-black/5 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-[var(--color-foreground-muted)]">
            disabled
          </span>
        )}
      </div>
      <p className="mb-1 text-[13px] text-[var(--color-foreground-muted)]">
        {model.provider}  ·  <code className="text-[12px]">{model.upstream_model}</code>
      </p>
      <p className="text-[12px] text-[var(--color-foreground-muted)]">
        Key: <code className="text-[12px]">{model.api_key_ref}</code>
      </p>

      {testResult !== undefined && (
        <div className="mt-2 text-[12px]">
          {testing ? (
            <span className="text-[var(--color-foreground-muted)]">Testing…</span>
          ) : testResult?.ok ? (
            <span className="text-emerald-700">
              ✓ OK · {testResult.duration_ms}ms
            </span>
          ) : (
            <span className="text-red-700">
              ✗ Failed
              {testResult?.status_code ? ` (${testResult.status_code})` : ''}
              {testResult?.error ? ` · ${testResult.error.slice(0, 80)}` : ''}
            </span>
          )}
        </div>
      )}

      <div className="mt-3 flex items-center justify-end gap-1">
        <Button variant="ghost" size="sm" onClick={onTest} disabled={testing}>
          <Zap className="mr-1 h-3.5 w-3.5" />
          Test
        </Button>
        <Button variant="ghost" size="sm" onClick={onEdit}>
          <Edit className="mr-1 h-3.5 w-3.5" />
          Edit
        </Button>
        <Button variant="ghost" size="sm" onClick={onDelete}>
          <Trash2 className="mr-1 h-3.5 w-3.5" />
          Delete
        </Button>
      </div>
    </div>
  )
}

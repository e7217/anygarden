import { useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { Plus, Edit, Trash2, Zap, Lock } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  useGatewaySecrets,
  type GatewaySecret,
} from '@/hooks/useLLMGateway'
import type { LLMGatewayOutletContext } from '@/pages/AdminLLMGatewayPage'
import { SecretDialog } from './SecretDialog'
import { cn } from '@/lib/utils'

/**
 * Encrypted API keys consumed by the litellm subprocess.
 *
 * The list never carries plaintext — ``value_preview`` is a masked
 * hint (prefix + …last4). Admins edit the full value via the secret
 * dialog; the server stores Fernet ciphertext.
 */

export function SecretsSection() {
  const { secrets, status, error, create, update, remove } =
    useGatewaySecrets()
  const { incrementPending } =
    useOutletContext<LLMGatewayOutletContext>()

  // ``null`` = closed; ``'new'`` = create; a row = edit.
  const [dialog, setDialog] = useState<GatewaySecret | 'new' | null>(null)

  const handleDelete = async (s: GatewaySecret) => {
    if (!window.confirm(`Delete secret "${s.env_var_name}"?`)) return
    try {
      await remove(s.env_var_name)
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
            Secrets
          </h1>
          <p className="mt-1 text-[13px] text-[var(--color-foreground-muted)]">
            API keys injected into the litellm subprocess. Values are encrypted at rest; the UI never shows plaintext.
          </p>
        </div>
        <Button onClick={() => setDialog('new')} size="sm">
          <Plus className="mr-1 h-3.5 w-3.5" />
          Add secret
        </Button>
      </header>

      {status === 'loading' && secrets.length === 0 && (
        <p className="text-[13px] text-[var(--color-foreground-muted)]">Loading secrets…</p>
      )}

      {status === 'error' && (
        <div className="rounded-[var(--radius-md)] border border-red-200 bg-red-50 px-3 py-2 text-[13px] text-red-900">
          Couldn't load secrets: {error}
        </div>
      )}

      {status === 'loaded' && secrets.length === 0 && (
        <div className="rounded-[var(--radius-md)] border border-dashed border-[var(--color-border)] px-6 py-12 text-center">
          <Lock className="mx-auto h-8 w-8 text-[var(--color-foreground-subtle)]" />
          <p className="mt-2 text-[14px] font-medium text-[var(--color-foreground)]">
            No secrets stored.
          </p>
          <p className="mt-1 text-[13px] text-[var(--color-foreground-muted)]">
            Add upstream API keys here. Models reference them by <code className="text-[12px]">env_var_name</code>.
          </p>
          <Button onClick={() => setDialog('new')} className="mt-4" size="sm">
            <Plus className="mr-1 h-3.5 w-3.5" />
            Add your first secret
          </Button>
        </div>
      )}

      <div className="flex flex-col gap-3">
        {secrets.map(s => (
          <SecretCard
            key={s.env_var_name}
            secret={s}
            onEdit={() => setDialog(s)}
            onDelete={() => handleDelete(s)}
          />
        ))}
      </div>

      {dialog && (
        <SecretDialog
          initial={dialog === 'new' ? null : dialog}
          onClose={() => setDialog(null)}
          onSubmit={async (envVarName, value) => {
            if (dialog === 'new') {
              await create(envVarName, value)
            } else {
              await update(dialog.env_var_name, value)
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
  secret: GatewaySecret
  onEdit: () => void
  onDelete: () => void
}

function SecretCard({ secret, onEdit, onDelete }: CardProps) {
  const statusBadge = renderStatusBadge(secret.last_test_status)

  return (
    <div className="rounded-[var(--radius-md)] border border-[var(--color-border)] bg-white p-4 shadow-whisper">
      <div className="mb-1 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Lock className="h-4 w-4 text-[var(--color-foreground-subtle)]" />
          <span className="font-semibold text-[var(--color-foreground)]">
            {secret.env_var_name}
          </span>
        </div>
        {statusBadge}
      </div>
      <p className="text-[13px] font-mono text-[var(--color-foreground-muted)]">
        {secret.value_preview}
      </p>
      <p className="mt-1 text-[11px] text-[var(--color-foreground-muted)]">
        {secret.last_tested_at
          ? `Tested ${relativeTime(secret.last_tested_at)}`
          : 'Never tested'}
      </p>

      <div className="mt-3 flex items-center justify-end gap-1">
        <Button
          variant="ghost" size="sm"
          onClick={() => alert('Secret test will ping any model that references this key — Phase 4 follow-up.')}
          disabled
          title="Coming soon — use the per-model Test button instead"
        >
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

function renderStatusBadge(status: string | null) {
  if (!status) {
    return (
      <span className={cn(
        'rounded-[var(--radius-sm)] px-1.5 py-0.5 text-[10px] uppercase tracking-wide',
        'bg-black/5 text-[var(--color-foreground-muted)]'
      )}>
        Not tested
      </span>
    )
  }
  if (status === 'ok') {
    return (
      <span className="rounded-[var(--radius-sm)] bg-emerald-50 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-emerald-700">
        Valid
      </span>
    )
  }
  return (
    <span className="rounded-[var(--radius-sm)] bg-red-50 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-red-700">
      {status}
    </span>
  )
}

function relativeTime(iso: string): string {
  const delta = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(delta / 60_000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  return `${days}d ago`
}

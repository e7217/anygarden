import { useState } from 'react'
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import type { GatewaySecret } from '@/hooks/useLLMGateway'

interface Props {
  /** null → create, otherwise edit (env_var_name is locked). */
  initial: GatewaySecret | null
  onClose: () => void
  onSubmit: (envVarName: string, value: string) => Promise<void>
}

/**
 * Add-or-edit dialog for a single secret row. The env var name is
 * immutable after creation (it's referenced by model rows); only the
 * plaintext value can change. Editing is what the Secrets section
 * surfaces as "Edit" — internally it's the PATCH /secrets endpoint.
 */

export function SecretDialog({ initial, onClose, onSubmit }: Props) {
  const [envVarName, setEnvVarName] = useState(initial?.env_var_name ?? '')
  const [value, setValue] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const isEdit = initial !== null

  const handleSubmit = async () => {
    setError(null)
    if (!envVarName.trim()) {
      setError('Env var name is required.')
      return
    }
    if (!value) {
      setError('Value is required.')
      return
    }
    setSubmitting(true)
    try {
      await onSubmit(envVarName.trim(), value)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose() }}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>
            {isEdit ? `Edit ${initial.env_var_name}` : 'Add secret'}
          </DialogTitle>
          <DialogDescription>
            {isEdit
              ? 'Enter a new value. The previous value is replaced and any test result is invalidated.'
              : 'The env var name is how model rows reference this key. The plaintext value is encrypted with the cluster\u2019s Fernet key before storage.'
            }
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-3 py-2">
          <div className="flex flex-col gap-1">
            <Label htmlFor="env_var_name">Env var name</Label>
            <Input
              id="env_var_name"
              placeholder="ANTHROPIC_API_KEY"
              value={envVarName}
              onChange={e => setEnvVarName(e.target.value)}
              disabled={isEdit}
              autoFocus={!isEdit}
            />
            <p className="text-[11px] text-[var(--color-foreground-muted)]">
              Uppercase with underscores, matches the provider's env var convention.
            </p>
          </div>

          <div className="flex flex-col gap-1">
            <Label htmlFor="value">
              {isEdit ? 'New value' : 'Value'}
            </Label>
            <Input
              id="value"
              type="password"
              placeholder={isEdit ? 'Enter new API key' : 'sk-ant-api03-...'}
              value={value}
              onChange={e => setValue(e.target.value)}
              autoFocus={isEdit}
              autoComplete="off"
            />
          </div>

          {error && (
            <p className="rounded-[var(--radius-sm)] border border-[var(--color-destructive)]/30 bg-[var(--color-destructive)]/10 px-2 py-1 text-[12px] text-[var(--color-destructive)]">
              {error}
            </p>
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={submitting}>
            {submitting ? 'Saving…' : 'Save'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

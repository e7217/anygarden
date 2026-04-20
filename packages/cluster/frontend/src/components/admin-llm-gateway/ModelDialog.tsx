import { useState } from 'react'
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useGatewaySecrets } from '@/hooks/useLLMGateway'
import type { GatewayModel, ModelCreateInput } from '@/hooks/useLLMGateway'

/**
 * Add-or-edit dialog for ``LLMGatewayModel``. Provider dropdown
 * prefills ``upstream_model`` with the provider's LiteLLM prefix so
 * admins don't have to remember the format (e.g. ``anthropic/...``).
 */

const PROVIDERS = [
  { id: 'anthropic', label: 'Anthropic', upstreamPrefix: 'anthropic/' },
  { id: 'openai', label: 'OpenAI', upstreamPrefix: 'openai/' },
  { id: 'bedrock', label: 'AWS Bedrock', upstreamPrefix: 'bedrock/' },
  { id: 'vertex_ai', label: 'Google Vertex', upstreamPrefix: 'vertex_ai/' },
  { id: 'azure', label: 'Azure OpenAI', upstreamPrefix: 'azure/' },
  { id: 'ollama', label: 'Ollama (local)', upstreamPrefix: 'ollama/' },
  { id: 'custom', label: 'Custom', upstreamPrefix: '' },
]

interface Props {
  initial: GatewayModel | null
  onClose: () => void
  onSubmit: (input: ModelCreateInput) => Promise<void>
}

export function ModelDialog({ initial, onClose, onSubmit }: Props) {
  const { secrets } = useGatewaySecrets()
  const [provider, setProvider] = useState(initial?.provider ?? 'anthropic')
  const [modelName, setModelName] = useState(initial?.model_name ?? '')
  const [upstream, setUpstream] = useState(initial?.upstream_model ?? '')
  const [apiKeyRef, setApiKeyRef] = useState(
    initial?.api_key_ref ?? (secrets[0]?.env_var_name ?? '')
  )
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleProviderChange = (next: string) => {
    setProvider(next)
    const meta = PROVIDERS.find(p => p.id === next)
    // Only prefill upstream if the user hasn't already written one
    // that doesn't match the old prefix — don't clobber manual edits.
    if (meta && !upstream.includes('/') && meta.upstreamPrefix) {
      setUpstream(meta.upstreamPrefix)
    }
  }

  const handleSubmit = async () => {
    setError(null)
    if (!modelName.trim() || !upstream.trim() || !apiKeyRef.trim()) {
      setError('Model name, upstream, and API key are all required.')
      return
    }
    setSubmitting(true)
    try {
      await onSubmit({
        model_name: modelName.trim(),
        provider,
        upstream_model: upstream.trim(),
        api_key_ref: apiKeyRef.trim(),
      })
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
            {initial ? `Edit ${initial.model_name}` : 'Add model'}
          </DialogTitle>
          <DialogDescription>
            Register a model the gateway will expose to agents via its{' '}
            <code className="text-[12px]">model_name</code>.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-3 py-2">
          <div className="flex flex-col gap-1">
            <Label htmlFor="provider">Provider</Label>
            <select
              id="provider"
              value={provider}
              onChange={e => handleProviderChange(e.target.value)}
              className="flex h-9 w-full rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-white px-3 text-[14px]"
            >
              {PROVIDERS.map(p => (
                <option key={p.id} value={p.id}>{p.label}</option>
              ))}
            </select>
          </div>

          <div className="flex flex-col gap-1">
            <Label htmlFor="model_name">Model name</Label>
            <Input
              id="model_name"
              placeholder="claude-sonnet-4-6"
              value={modelName}
              onChange={e => setModelName(e.target.value)}
              autoFocus
            />
            <p className="text-[11px] text-[var(--color-foreground-muted)]">
              What agents reference in their requests.
            </p>
          </div>

          <div className="flex flex-col gap-1">
            <Label htmlFor="upstream">Upstream model</Label>
            <Input
              id="upstream"
              placeholder="anthropic/claude-sonnet-4-6"
              value={upstream}
              onChange={e => setUpstream(e.target.value)}
            />
            <p className="text-[11px] text-[var(--color-foreground-muted)]">
              LiteLLM-native identifier (provider prefix + model id).
            </p>
          </div>

          <div className="flex flex-col gap-1">
            <Label htmlFor="api_key_ref">API key</Label>
            {secrets.length > 0 ? (
              <select
                id="api_key_ref"
                value={apiKeyRef}
                onChange={e => setApiKeyRef(e.target.value)}
                className="flex h-9 w-full rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-white px-3 text-[14px]"
              >
                {secrets.map(s => (
                  <option key={s.env_var_name} value={s.env_var_name}>
                    {s.env_var_name}
                  </option>
                ))}
              </select>
            ) : (
              <>
                <Input
                  id="api_key_ref"
                  placeholder="ANTHROPIC_API_KEY"
                  value={apiKeyRef}
                  onChange={e => setApiKeyRef(e.target.value)}
                />
                <p className="text-[11px] text-[var(--color-foreground-muted)]">
                  No secrets registered yet. Use the Secrets section to add one, then this will become a dropdown.
                </p>
              </>
            )}
          </div>

          {error && (
            <p className="rounded-[var(--radius-sm)] border border-red-200 bg-red-50 px-2 py-1 text-[12px] text-red-900">
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

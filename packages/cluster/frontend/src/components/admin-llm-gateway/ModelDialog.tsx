import { useState } from 'react'
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useGatewaySecrets, fetchOllamaModels } from '@/hooks/useLLMGateway'
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
  // ``ollama_chat/`` (LiteLLM's native /api/chat path) preserves
  // OpenAI tool_calls and free-form prose responses. The legacy
  // ``ollama/`` prefix triggers LiteLLM's "functions_unsupported_model"
  // path which clamps the upstream call to ``format: json`` — that
  // forces tool-using models (qwen3, Llama 3.1+, …) to wrap their
  // final summary in a fake JSON envelope like ``{"tool_code": ...,
  // "tool_output": "<actual answer>"}`` instead of plain prose.
  // ``config_writer`` rewrites any stored ``ollama/`` to ``ollama_chat/``
  // at render time too, so older rows keep working; this prefix change
  // just stores the canonical form for new entries.
  { id: 'ollama', label: 'Ollama (local)', upstreamPrefix: 'ollama_chat/' },
  { id: 'vllm', label: 'vLLM (local)', upstreamPrefix: 'openai/' },
  { id: 'custom', label: 'Custom', upstreamPrefix: '' },
]

// Local/self-hosted providers: api_key_ref becomes optional (most
// Ollama/vLLM setups don't check Authorization) and the api_base URL
// field is exposed so the admin can point at a LAN host instead of
// relying on LiteLLM's per-provider default (``localhost:11434`` etc).
// Backend normalises blank api_key_ref to the ``OLLAMA_DUMMY`` sentinel
// for these providers — see ``_normalise_api_key_ref`` in llm_gateway.py.
const LOCAL_PROVIDERS = new Set(['ollama', 'vllm', 'custom'])

const API_KEY_PLACEHOLDER: Record<string, string> = {
  anthropic: 'ANTHROPIC_API_KEY',
  openai: 'OPENAI_API_KEY',
  bedrock: 'AWS_BEDROCK_API_KEY',
  vertex_ai: 'GOOGLE_VERTEX_API_KEY',
  azure: 'AZURE_API_KEY',
  ollama: '(optional for Ollama)',
  vllm: '(optional for vLLM)',
  custom: '(leave blank if endpoint has no auth)',
}

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
  // Backend stores the OLLAMA_DUMMY sentinel for local providers when the
  // admin left api_key_ref blank. Surfacing that sentinel back into the
  // Edit form as a pre-filled value would look like a real env var name;
  // blank it out so the placeholder text guides the user instead.
  const initialApiKeyRef =
    initial?.api_key_ref && initial.api_key_ref !== 'OLLAMA_DUMMY'
      ? initial.api_key_ref
      : (secrets[0]?.env_var_name ?? '')
  const [apiKeyRef, setApiKeyRef] = useState(initialApiKeyRef)
  // Unpack extra_params.api_base into its own form field so local-provider
  // admins see it as a first-class setting. Other keys in extra_params
  // (temperature, custom headers, …) are merged back untouched at submit.
  const [apiBase, setApiBase] = useState<string>(() => {
    const existing = (initial?.extra_params as Record<string, unknown> | null | undefined)?.api_base
    return typeof existing === 'string' ? existing : ''
  })
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // #410 — Ollama model discovery. Populated by the "Load models" button;
  // selecting an entry fills upstream_model + model_name so an operator
  // can't fat-finger the upstream id (the cause of #408).
  const [ollamaModels, setOllamaModels] = useState<string[]>([])
  const [loadingModels, setLoadingModels] = useState(false)
  const [modelsError, setModelsError] = useState<string | null>(null)

  const isLocal = LOCAL_PROVIDERS.has(provider)

  const handleProviderChange = (next: string) => {
    setProvider(next)
    // Discovered list belongs to the previous provider — clear it so a
    // stale Ollama list can't linger after switching to, say, OpenAI.
    setOllamaModels([])
    setModelsError(null)
    const meta = PROVIDERS.find(p => p.id === next)
    // Only prefill upstream if the user hasn't already written one
    // that doesn't match the old prefix — don't clobber manual edits.
    if (meta && !upstream.includes('/') && meta.upstreamPrefix) {
      setUpstream(meta.upstreamPrefix)
    }
  }

  const handleLoadOllamaModels = async () => {
    setLoadingModels(true)
    setModelsError(null)
    try {
      const res = await fetchOllamaModels(apiBase.trim())
      if (!res.ok) {
        setModelsError(res.error || 'Could not reach Ollama.')
        setOllamaModels([])
      } else if (res.models.length === 0) {
        setModelsError('No models installed — run `ollama pull <model>` and retry.')
        setOllamaModels([])
      } else {
        setOllamaModels(res.models)
      }
    } catch (err) {
      setModelsError(err instanceof Error ? err.message : String(err))
      setOllamaModels([])
    } finally {
      setLoadingModels(false)
    }
  }

  const handleSelectOllamaModel = (name: string) => {
    if (!name) return
    setUpstream('ollama_chat/' + name)
    setModelName(name)
  }

  const handleSubmit = async () => {
    setError(null)
    if (!modelName.trim() || !upstream.trim()) {
      setError('Model name and upstream model are required.')
      return
    }
    if (!isLocal && !apiKeyRef.trim()) {
      setError(`Provider '${provider}' requires an API key reference.`)
      return
    }

    // Preserve any non-api_base keys the admin previously stored
    // (e.g. temperature, max_tokens) — the api_base field is the only
    // one this dialog owns right now, but that may change later.
    const existingExtras =
      (initial?.extra_params as Record<string, unknown> | null | undefined) ?? {}
    const nextExtras: Record<string, unknown> = { ...existingExtras }
    const trimmedBase = apiBase.trim()
    if (trimmedBase) {
      nextExtras.api_base = trimmedBase
    } else {
      delete nextExtras.api_base
    }
    const extraParamsPayload =
      Object.keys(nextExtras).length > 0 ? nextExtras : null

    setSubmitting(true)
    try {
      await onSubmit({
        model_name: modelName.trim(),
        provider,
        upstream_model: upstream.trim(),
        // Empty string is fine for local providers — backend normalises
        // it to the OLLAMA_DUMMY sentinel. Trim keeps whitespace-only
        // input from silently passing either branch.
        api_key_ref: apiKeyRef.trim(),
        extra_params: extraParamsPayload,
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
            <Label htmlFor="api_key_ref">
              API key{isLocal ? ' (optional)' : ''}
            </Label>
            {secrets.length > 0 && !isLocal ? (
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
                  placeholder={API_KEY_PLACEHOLDER[provider] ?? 'ENV_VAR_NAME'}
                  value={apiKeyRef}
                  onChange={e => setApiKeyRef(e.target.value)}
                />
                <p className="text-[11px] text-[var(--color-foreground-muted)]">
                  {isLocal
                    ? 'Most local endpoints ignore Authorization — leave blank unless your server requires one.'
                    : secrets.length > 0
                    ? 'Or type a new env var name not yet registered in Secrets.'
                    : 'No secrets registered yet. Use the Secrets section to add one, then this will become a dropdown.'}
                </p>
              </>
            )}
          </div>

          {isLocal && (
            <div className="flex flex-col gap-1">
              <Label htmlFor="api_base">API base URL</Label>
              <Input
                id="api_base"
                placeholder={
                  provider === 'ollama'
                    ? 'http://localhost:11434'
                    : provider === 'vllm'
                    ? 'http://localhost:8000/v1'
                    : 'https://…'
                }
                value={apiBase}
                onChange={e => setApiBase(e.target.value)}
              />
              <p className="text-[11px] text-[var(--color-foreground-muted)]">
                Leave blank to use LiteLLM's provider default. Point this at a remote host when Ollama/vLLM runs off-server.
              </p>
              {provider === 'ollama' && (
                <div className="mt-1 flex flex-col gap-1">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="self-start"
                    onClick={handleLoadOllamaModels}
                    disabled={loadingModels}
                  >
                    {loadingModels ? 'Loading…' : 'Load models'}
                  </Button>
                  {ollamaModels.length > 0 && (
                    <select
                      aria-label="Installed Ollama models"
                      defaultValue=""
                      onChange={e => handleSelectOllamaModel(e.target.value)}
                      className="flex h-9 w-full rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-white px-3 text-[14px]"
                    >
                      <option value="" disabled>
                        Select a model…
                      </option>
                      {ollamaModels.map(m => (
                        <option key={m} value={m}>
                          {m}
                        </option>
                      ))}
                    </select>
                  )}
                  {modelsError && (
                    <p className="text-[11px] text-red-900">{modelsError}</p>
                  )}
                </div>
              )}
            </div>
          )}

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

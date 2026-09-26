// Direct model server fields for Pi in the Create
// Agent dialog. Owns only presentation and the model probe; the dialog owns
// the draft state and the create → credential → endpoint sequence.
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  type DiscoveredModel,
  type EndpointProtocol,
  defaultEndpointProtocol,
  discoverEndpointModels,
  isValidEndpointUrl,
} from '@/lib/engineEndpoints'

export interface EndpointDraft {
  enabled: boolean
  baseUrl: string
  protocol: EndpointProtocol
  auth: 'none' | 'key'
  apiKey: string
}

export function emptyEndpointDraft(engine: string): EndpointDraft {
  return { enabled: false, baseUrl: '', protocol: defaultEndpointProtocol(engine), auth: 'none', apiKey: '' }
}

/** A draft is ready when it is disabled, or complete enough to submit. */
export function endpointDraftReady(draft: EndpointDraft, model: string): boolean {
  if (!draft.enabled) return true
  return isValidEndpointUrl(draft.baseUrl) && !!model.trim() && (draft.auth === 'none' || !!draft.apiKey)
}

export default function CreateAgentEndpointSection({ engine, draft, onChange, onModelsLoaded, selectClassName }: {
  engine: string
  draft: EndpointDraft
  onChange: (next: EndpointDraft) => void
  onModelsLoaded: (models: DiscoveredModel[]) => void
  selectClassName: string
}) {
  const [loading, setLoading] = useState(false)
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const urlValid = isValidEndpointUrl(draft.baseUrl)
  const update = (patch: Partial<EndpointDraft>) => onChange({ ...draft, ...patch })

  async function loadModels() {
    setLoading(true); setError(''); setStatus('')
    try {
      const result = await discoverEndpointModels({
        base_url: draft.baseUrl,
        ...(draft.auth === 'key' && draft.apiKey ? { api_key: draft.apiKey } : {}),
      })
      onModelsLoaded(result.models)
      const where = result.reachable_from === 'server' ? 'reachable from the AnyGarden server' : `reachable from ${result.reachable_from}`
      setStatus(`${result.models.length} ${result.models.length === 1 ? 'model' : 'models'} found · ${where}`)
    } catch (e) {
      onModelsLoaded([])
      setError(e instanceof Error ? e.message : 'Unable to load models')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-3 rounded-[var(--radius-md)] border border-[var(--color-border)] p-3">
      <>
          <p className="text-xs text-[var(--color-foreground-muted)]">
            OpenAI-compatible server such as vLLM, llama.cpp or Ollama (<code>/v1</code>). The agent calls it from its machine.
          </p>
          <div className="space-y-2">
            <Label htmlFor="endpoint-base-url">Base URL</Label>
            <Input id="endpoint-base-url" value={draft.baseUrl} placeholder="http://localhost:8000/v1"
              onChange={e => update({ baseUrl: e.target.value.trim() })} aria-invalid={!!draft.baseUrl && !urlValid} />
            {draft.baseUrl && !urlValid && (
              <p className="text-xs text-[var(--color-warning)]">Enter an HTTP(S) URL without credentials, query or fragment.</p>
            )}
          </div>
          <div className="space-y-2">
            <Label htmlFor="endpoint-protocol">API protocol</Label>
            <select id="endpoint-protocol" className={selectClassName} value={draft.protocol}
              onChange={e => update({ protocol: e.target.value as EndpointProtocol })}>
              {engine === 'pi-cli' && <option value="chat-completions">Chat Completions</option>}
              <option value="responses">Responses</option>
            </select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="endpoint-auth">Authentication</Label>
            <select id="endpoint-auth" className={selectClassName} value={draft.auth}
              onChange={e => update({ auth: e.target.value as EndpointDraft['auth'], apiKey: '' })}>
              <option value="none">No authentication</option>
              <option value="key">API key</option>
            </select>
          </div>
          {draft.auth === 'key' && (
            <div className="space-y-2">
              <Label htmlFor="endpoint-api-key">API key</Label>
              <Input id="endpoint-api-key" type="password" autoComplete="new-password" value={draft.apiKey}
                onChange={e => update({ apiKey: e.target.value })} />
              <p className="text-xs text-[var(--color-foreground-muted)]">Stored encrypted for this agent after creation; it cannot be read back.</p>
            </div>
          )}
          <div className="flex items-center gap-2">
            <Button type="button" variant="outline" size="sm" className="min-h-11" disabled={!urlValid || loading || (draft.auth === 'key' && !draft.apiKey)}
              onClick={() => void loadModels()}>
              {loading ? 'Loading…' : 'Load models'}
            </Button>
            {status && <span role="status" className="text-xs text-[var(--color-foreground-muted)]">{status}</span>}
          </div>
          {error && <p role="alert" className="text-xs text-[var(--color-warning)]">{error}</p>}
      </>
    </div>
  )
}

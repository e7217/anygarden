// #685 — direct model endpoint helpers shared by the create dialog and the
// agent settings panel. Secrets go only to write-only endpoints and are never
// kept beyond the request that needs them.
import { apiFetch } from '@/lib/api'

export type EndpointProtocol = 'responses' | 'chat-completions'

export interface DiscoveredModel {
  id: string
  max_model_len: number | null
}

export interface ModelDiscovery {
  models: DiscoveredModel[]
  /** Where the probe ran — `server` means reachable from the AnyGarden server. */
  reachable_from: string
}

export type ModelDiscoveryRequest =
  | { base_url: string; api_key?: string }
  | { base_url: string; agent_id: string; credential_ref: string }

async function detail(response: Response, fallback: string): Promise<string> {
  const data = await response.json().catch(() => ({}))
  return typeof data.detail === 'string' ? data.detail : fallback
}

/** Mirrors the server's URL policy so obvious mistakes fail before a request. */
export function isValidEndpointUrl(value: string): boolean {
  if (!value || /\s/.test(value) || value.includes('\\')) return false
  try {
    const url = new URL(value)
    return (url.protocol === 'http:' || url.protocol === 'https:')
      && !!url.hostname && !url.username && !url.password && !url.search && !url.hash
  } catch {
    return false
  }
}

export function defaultEndpointProtocol(engine: string): EndpointProtocol {
  return engine === 'pi-cli' ? 'chat-completions' : 'responses'
}

export async function discoverEndpointModels(request: ModelDiscoveryRequest): Promise<ModelDiscovery> {
  const response = await apiFetch('/api/v1/engine-endpoints/models', {
    method: 'POST',
    body: JSON.stringify(request),
  })
  if (!response.ok) throw new Error(await detail(response, 'Unable to load models from this endpoint'))
  return response.json()
}

export async function storeEndpointCredential(agentId: string, value: string, label = 'Endpoint API key'): Promise<string> {
  const response = await apiFetch(`/api/v1/agents/${agentId}/endpoint/credentials`, {
    method: 'POST',
    body: JSON.stringify({ value, label }),
  })
  if (!response.ok) throw new Error(await detail(response, 'Unable to store the API key'))
  return (await response.json()).id
}

export async function applyEndpoint(agentId: string, config: {
  provider: string; model: string; base_url: string; api_protocol: EndpointProtocol; credential_ref: string | null
}): Promise<void> {
  const response = await apiFetch(`/api/v1/agents/${agentId}/endpoint`, {
    method: 'PUT',
    body: JSON.stringify(config),
  })
  if (!response.ok) throw new Error(await detail(response, 'Unable to apply the endpoint'))
}

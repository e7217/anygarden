// Direct model server fields for Pi in the Create
// Agent dialog. Owns only presentation and the model probe; the dialog owns
// the draft state and the create → credential → endpoint sequence.
import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { useLocale } from '@/i18n/LocaleProvider'
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
  const { t } = useLocale()
  const [loading, setLoading] = useState(false)
  const [status, setStatus] = useState<{ count: number; reachableFrom: string } | null>(null)
  const [error, setError] = useState('')
  const probeRevision = useRef(0)
  useEffect(() => {
    probeRevision.current += 1
    setLoading(false); setStatus(null); setError('')
    return () => { probeRevision.current += 1 }
  }, [engine, draft.baseUrl, draft.auth, draft.apiKey])
  const urlValid = isValidEndpointUrl(draft.baseUrl)
  const update = (patch: Partial<EndpointDraft>) => onChange({ ...draft, ...patch })

  async function loadModels() {
    const revision = ++probeRevision.current
    setLoading(true); setError(''); setStatus(null)
    try {
      const result = await discoverEndpointModels({
        base_url: draft.baseUrl,
        ...(draft.auth === 'key' && draft.apiKey ? { api_key: draft.apiKey } : {}),
      })
      if (revision !== probeRevision.current) return
      onModelsLoaded(result.models)
      setStatus({ count: result.models.length, reachableFrom: result.reachable_from })
    } catch (e) {
      if (revision !== probeRevision.current) return
      onModelsLoaded([])
      setError(e instanceof Error ? e.message : t('admin.endpoint.loadFailed'))
    } finally {
      if (revision === probeRevision.current) setLoading(false)
    }
  }

  return (
    <div className="space-y-3 rounded-[var(--radius-md)] border border-[var(--color-border)] p-3">
      <>
          <p className="text-xs text-[var(--color-foreground-muted)]">
            {t('admin.endpoint.description')}
          </p>
          <div className="space-y-2">
            <Label htmlFor="endpoint-base-url">{t('admin.endpoint.baseUrl')}</Label>
            <Input id="endpoint-base-url" value={draft.baseUrl} placeholder="http://localhost:8000/v1"
              onChange={e => update({ baseUrl: e.target.value.trim() })} aria-invalid={!!draft.baseUrl && !urlValid} />
            {draft.baseUrl && !urlValid && (
              <p className="text-xs text-[var(--color-warning)]">{t('admin.endpoint.invalidUrl')}</p>
            )}
          </div>
          <div className="space-y-2">
            <Label htmlFor="endpoint-protocol">{t('admin.endpoint.apiProtocol')}</Label>
            <Select id="endpoint-protocol" className={selectClassName} value={draft.protocol}
              onChange={e => update({ protocol: e.target.value as EndpointProtocol })}>
              {engine === 'pi-cli' && <option value="chat-completions">Chat Completions</option>}
              <option value="responses">Responses</option>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="endpoint-auth">{t('admin.endpoint.authentication')}</Label>
            <Select id="endpoint-auth" className={selectClassName} value={draft.auth}
              onChange={e => update({ auth: e.target.value as EndpointDraft['auth'], apiKey: '' })}>
              <option value="none">{t('admin.endpoint.noAuthentication')}</option>
              <option value="key">{t('admin.endpoint.apiKey')}</option>
            </Select>
          </div>
          {draft.auth === 'key' && (
            <div className="space-y-2">
              <Label htmlFor="endpoint-api-key">{t('admin.endpoint.apiKey')}</Label>
              <Input id="endpoint-api-key" type="password" autoComplete="new-password" value={draft.apiKey}
                onChange={e => update({ apiKey: e.target.value })} />
              <p className="text-xs text-[var(--color-foreground-muted)]">{t('admin.endpoint.keyHint')}</p>
            </div>
          )}
          <div className="flex items-center gap-2">
            <Button type="button" variant="outline" size="sm"  disabled={!urlValid || loading || (draft.auth === 'key' && !draft.apiKey)}
              onClick={() => void loadModels()}>
              {loading ? t('admin.endpoint.loading') : t('admin.endpoint.loadModels')}
            </Button>
            {status && <span role="status" className="text-xs text-[var(--color-foreground-muted)]">{status.reachableFrom === 'server'
              ? t('admin.endpoint.modelsFoundServer', { count: status.count })
              : t('admin.endpoint.modelsFoundHost', { count: status.count, host: status.reachableFrom })}</span>}
          </div>
          {error && <p role="alert" className="text-xs text-[var(--color-warning)]">{error}</p>}
      </>
    </div>
  )
}

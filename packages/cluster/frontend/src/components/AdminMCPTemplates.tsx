import { useCallback, useEffect, useMemo, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription,
} from '@/components/ui/dialog'
import { Plug, Plus, Trash2, RefreshCw, Link as LinkIcon } from 'lucide-react'
import { apiFetch } from '@/lib/api'
import { useAgents } from '@/hooks/useAgents'

/**
 * MCP server template catalog admin page (#124).
 *
 * Two tabs:
 * - Builtin: the shipped templates (github / slack / notion / linear / filesystem).
 *   Read-only.
 * - Custom: admin-authored templates. Create / edit / delete.
 *
 * Per-agent attach lives inline on each row so the admin can wire a
 * template to an agent without leaving the page.
 */

const SUPPORTED_ENGINES = ['claude-code', 'codex', 'gemini-cli'] as const
type EngineId = typeof SUPPORTED_ENGINES[number]

interface Template {
  id: string
  name: string
  display_name: string
  description: string | null
  icon: string | null
  config_per_engine: Record<string, Record<string, unknown>>
  required_env_vars: string[]
  supported_engines: string[]
  source: 'builtin' | 'custom'
  created_by: string | null
  created_at: string
  updated_at: string
  instance_count: number
}

interface Instance {
  id: string
  template_id: string
  template_name: string
  agent_id: string
  enabled: boolean
  has_credentials: boolean
  required_env_vars: string[]
  created_at: string
  updated_at: string
}

type Tab = 'builtin' | 'custom'

export default function AdminMCPTemplates() {
  const [templates, setTemplates] = useState<Template[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<Tab>('builtin')
  const { agents, fetchAgents } = useAgents()

  // Per-agent instance map, keyed by agent_id.
  const [instancesByAgent, setInstancesByAgent] = useState<Record<string, Instance[]>>({})
  const [attachTarget, setAttachTarget] = useState<Template | null>(null)
  const [editorTarget, setEditorTarget] = useState<Template | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const resp = await apiFetch('/api/v1/admin/mcp-templates')
      if (!resp.ok) throw new Error(`GET /mcp-templates → ${resp.status}`)
      setTemplates(await resp.json())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  const loadInstances = useCallback(async (agentIds: string[]) => {
    const next: Record<string, Instance[]> = {}
    await Promise.all(agentIds.map(async (aid) => {
      const resp = await apiFetch(`/api/v1/admin/agents/${aid}/mcp-instances`)
      if (resp.ok) {
        next[aid] = (await resp.json()) as Instance[]
      } else {
        next[aid] = []
      }
    }))
    setInstancesByAgent(next)
  }, [])

  useEffect(() => {
    void load()
    void fetchAgents()
  }, [load, fetchAgents])

  useEffect(() => {
    if (agents.length > 0) {
      void loadInstances(agents.map(a => a.id))
    }
  }, [agents, loadInstances])

  const visibleTemplates = useMemo(
    () => templates.filter(t => t.source === activeTab),
    [templates, activeTab],
  )

  const handleDelete = useCallback(async (template: Template) => {
    if (template.source === 'builtin') return
    if (!window.confirm(`"${template.display_name}" 템플릿을 삭제하시겠습니까?`)) return
    const resp = await apiFetch(`/api/v1/admin/mcp-templates/${template.id}`, {
      method: 'DELETE',
    })
    if (resp.status === 204) {
      await load()
    } else {
      let detail = `Delete failed (${resp.status})`
      try {
        const body = await resp.json()
        if (body?.detail) detail = body.detail
      } catch { /* ignore */ }
      setError(detail)
    }
  }, [load])

  return (
    <div className="max-w-4xl p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-[var(--color-foreground)]">MCP Servers</h1>
          <p className="text-sm text-[var(--color-foreground-muted)]">
            Register Model Context Protocol servers and attach them to agents.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={() => void load()} disabled={loading}>
            <RefreshCw className={`mr-1 h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </Button>
          <Button size="sm" onClick={() => setEditorTarget({
            id: '',
            name: '',
            display_name: '',
            description: '',
            icon: null,
            config_per_engine: {},
            required_env_vars: [],
            supported_engines: [],
            source: 'custom',
            created_by: null,
            created_at: '',
            updated_at: '',
            instance_count: 0,
          })}>
            <Plus className="mr-1 h-3.5 w-3.5" /> New template
          </Button>
        </div>
      </div>

      {error && (
        <div className="rounded-[var(--radius-sm)] border border-[rgba(0,0,0,0.1)] bg-[color:color-mix(in_srgb,var(--color-danger)_8%,transparent)] p-3 text-sm text-[var(--color-danger)]">
          {error}
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-1 rounded-[var(--radius-sm)] border border-[rgba(0,0,0,0.1)] bg-white p-1 w-fit">
        {(['builtin', 'custom'] as Tab[]).map(tab => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`rounded-[var(--radius-sm)] px-3 py-1.5 text-xs font-medium transition-colors ${
              activeTab === tab
                ? 'bg-[var(--color-background)] text-[var(--color-foreground)]'
                : 'text-[var(--color-foreground-muted)] hover:text-[var(--color-foreground)]'
            }`}
          >
            {tab === 'builtin' ? 'Builtin' : 'Custom'}
          </button>
        ))}
      </div>

      {visibleTemplates.length === 0 && !loading ? (
        <div className="rounded-[var(--radius-lg)] border border-[rgba(0,0,0,0.1)] bg-white px-6 py-10 text-center shadow-[var(--shadow-card)]">
          <Plug
            className="mx-auto mb-3 h-8 w-8 text-[var(--color-foreground-subtle)]"
            strokeWidth={1.5}
          />
          <p className="text-sm text-[var(--color-foreground-muted)]">
            {activeTab === 'builtin'
              ? '빌트인 템플릿이 아직 시드되지 않았습니다. 서버를 재시작하세요.'
              : '등록된 커스텀 템플릿이 없습니다.'}
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {visibleTemplates.map(template => (
            <div
              key={template.id}
              className="rounded-[var(--radius-lg)] border border-[rgba(0,0,0,0.1)] bg-white px-4 py-3 shadow-[var(--shadow-card)]"
              data-testid={`mcp-template-row-${template.id}`}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <h3 className="text-sm font-semibold text-[var(--color-foreground)]">
                      {template.display_name}
                    </h3>
                    <Badge variant="outline">
                      <code className="text-[11px]">{template.name}</code>
                    </Badge>
                    {template.source === 'builtin' && (
                      <Badge variant="outline" className="text-[var(--color-foreground-muted)]">
                        builtin
                      </Badge>
                    )}
                  </div>
                  {template.description && (
                    <p className="mt-1 text-xs text-[var(--color-foreground-muted)]">
                      {template.description}
                    </p>
                  )}
                  <div className="mt-2 flex flex-wrap items-center gap-1">
                    {template.supported_engines.map(engine => (
                      <Badge key={engine} variant="outline" className="text-[10px]">
                        {engine}
                      </Badge>
                    ))}
                    {template.required_env_vars.length > 0 && (
                      <Badge
                        variant="outline"
                        className="text-[10px] text-[var(--color-foreground-muted)]"
                        title={template.required_env_vars.join('\n')}
                      >
                        {template.required_env_vars.length} env var{template.required_env_vars.length === 1 ? '' : 's'}
                      </Badge>
                    )}
                  </div>
                  <p className="mt-2 text-xs text-[var(--color-foreground-muted)]">
                    {template.instance_count} instance{template.instance_count === 1 ? '' : 's'}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setAttachTarget(template)}
                    data-testid={`mcp-template-attach-${template.id}`}
                  >
                    <LinkIcon className="mr-1 h-3.5 w-3.5" />
                    Attach
                  </Button>
                  {template.source === 'custom' && (
                    <>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setEditorTarget(template)}
                      >
                        Edit
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => void handleDelete(template)}
                        aria-label={`Delete ${template.display_name}`}
                      >
                        <Trash2 className="h-4 w-4 text-[var(--color-danger)]" />
                      </Button>
                    </>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {attachTarget && (
        <AttachDialog
          template={attachTarget}
          agents={agents}
          instancesByAgent={instancesByAgent}
          onClose={() => {
            setAttachTarget(null)
            void load()
            void loadInstances(agents.map(a => a.id))
          }}
        />
      )}

      {editorTarget && (
        <CustomEditorDialog
          template={editorTarget}
          onClose={() => setEditorTarget(null)}
          onSaved={async () => {
            setEditorTarget(null)
            await load()
          }}
        />
      )}
    </div>
  )
}


// ── Attach dialog ─────────────────────────────────────────────────

interface AttachDialogProps {
  template: Template
  agents: { id: string; name: string; engine: string }[]
  instancesByAgent: Record<string, Instance[]>
  onClose: () => void
}

function AttachDialog({ template, agents, instancesByAgent, onClose }: AttachDialogProps) {
  const eligibleAgents = agents.filter(a => template.supported_engines.includes(a.engine))
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(
    eligibleAgents[0]?.id ?? null,
  )
  const [envValues, setEnvValues] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const existing = selectedAgentId
    ? (instancesByAgent[selectedAgentId] ?? []).find(i => i.template_id === template.id)
    : undefined

  const handleAttach = useCallback(async () => {
    if (!selectedAgentId) return
    setBusy(true)
    setError(null)
    try {
      const resp = await apiFetch(
        `/api/v1/admin/agents/${selectedAgentId}/mcp-instances`,
        {
          method: 'POST',
          body: JSON.stringify({
            template_id: template.id,
            env_values: envValues,
          }),
        },
      )
      if (!resp.ok) {
        let detail = `Attach failed (${resp.status})`
        try {
          const body = await resp.json()
          if (body?.detail) detail = body.detail
        } catch { /* ignore */ }
        throw new Error(detail)
      }
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }, [selectedAgentId, template.id, envValues, onClose])

  const handleDetach = useCallback(async () => {
    if (!selectedAgentId || !existing) return
    const resp = await apiFetch(
      `/api/v1/admin/agents/${selectedAgentId}/mcp-instances/${existing.id}`,
      { method: 'DELETE' },
    )
    if (resp.status === 204) onClose()
  }, [selectedAgentId, existing, onClose])

  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose() }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            Attach <code>{template.name}</code>
          </DialogTitle>
          <DialogDescription>
            Wire this MCP server to an agent. Credentials are Fernet-encrypted
            at rest; they&apos;re rendered into the engine&apos;s settings file
            at spawn time.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3 py-2">
          {eligibleAgents.length === 0 ? (
            <p className="text-sm text-[var(--color-foreground-muted)]">
              No agents running on a supported engine ({template.supported_engines.join(', ')}).
            </p>
          ) : (
            <>
              <div>
                <Label htmlFor="mcp-attach-agent">Agent</Label>
                <select
                  id="mcp-attach-agent"
                  value={selectedAgentId ?? ''}
                  onChange={(e) => {
                    setSelectedAgentId(e.target.value)
                    setEnvValues({})
                  }}
                  className="w-full rounded-[var(--radius-sm)] border border-[rgba(0,0,0,0.1)] bg-white px-2 py-1.5 text-sm"
                >
                  {eligibleAgents.map(a => (
                    <option key={a.id} value={a.id}>
                      {a.name} ({a.engine})
                    </option>
                  ))}
                </select>
              </div>

              {existing && (
                <div className="rounded-[var(--radius-sm)] border border-[rgba(0,0,0,0.1)] bg-[var(--color-background)] px-3 py-2 text-xs text-[var(--color-foreground-muted)]">
                  Already attached. Re-entering values will overwrite the
                  stored credentials.
                </div>
              )}

              {template.required_env_vars.map(varName => (
                <div key={varName}>
                  <Label htmlFor={`mcp-env-${varName}`}>{varName}</Label>
                  <Input
                    id={`mcp-env-${varName}`}
                    type="password"
                    autoComplete="off"
                    value={envValues[varName] ?? ''}
                    onChange={(e) => setEnvValues(v => ({ ...v, [varName]: e.target.value }))}
                  />
                </div>
              ))}

              {error && (
                <p className="text-xs text-[var(--color-danger)]">{error}</p>
              )}
            </>
          )}
        </div>
        <DialogFooter>
          {existing && (
            <Button
              variant="ghost"
              onClick={() => void handleDetach()}
              className="mr-auto text-[var(--color-danger)]"
            >
              Detach
            </Button>
          )}
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button
            onClick={() => void handleAttach()}
            disabled={
              busy || !selectedAgentId
              || template.required_env_vars.some(v => !envValues[v])
            }
          >
            {busy ? 'Saving…' : existing ? 'Update' : 'Attach'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}


// ── Custom template editor ────────────────────────────────────────

interface CustomEditorProps {
  template: Template
  onClose: () => void
  onSaved: () => Promise<void>
}

function CustomEditorDialog({ template, onClose, onSaved }: CustomEditorProps) {
  const isCreate = template.id === ''
  const [name, setName] = useState(template.name)
  const [displayName, setDisplayName] = useState(template.display_name)
  const [description, setDescription] = useState(template.description ?? '')
  const [requiredEnvText, setRequiredEnvText] = useState(
    template.required_env_vars.join(', '),
  )
  const [supportedEngines, setSupportedEngines] = useState<Set<EngineId>>(
    new Set(template.supported_engines as EngineId[]),
  )
  const [configText, setConfigText] = useState(
    JSON.stringify(
      template.config_per_engine && Object.keys(template.config_per_engine).length > 0
        ? template.config_per_engine
        : { 'claude-code': { command: 'npx', args: [], env: {} } },
      null, 2,
    ),
  )
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const toggleEngine = (engine: EngineId) => {
    setSupportedEngines(prev => {
      const next = new Set(prev)
      if (next.has(engine)) next.delete(engine)
      else next.add(engine)
      return next
    })
  }

  const handleSave = useCallback(async () => {
    setBusy(true)
    setError(null)
    try {
      let configParsed: Record<string, Record<string, unknown>>
      try {
        configParsed = JSON.parse(configText)
      } catch (e) {
        throw new Error(`config_per_engine is not valid JSON: ${(e as Error).message}`)
      }
      const body = {
        name: name.trim(),
        display_name: displayName.trim(),
        description: description.trim() || null,
        icon: null as string | null,
        config_per_engine: configParsed,
        required_env_vars: requiredEnvText
          .split(',')
          .map(s => s.trim())
          .filter(Boolean),
        supported_engines: Array.from(supportedEngines),
      }
      const resp = isCreate
        ? await apiFetch('/api/v1/admin/mcp-templates', {
            method: 'POST', body: JSON.stringify(body),
          })
        : await apiFetch(`/api/v1/admin/mcp-templates/${template.id}`, {
            method: 'PUT',
            body: JSON.stringify({
              display_name: body.display_name,
              description: body.description,
              config_per_engine: body.config_per_engine,
              required_env_vars: body.required_env_vars,
              supported_engines: body.supported_engines,
            }),
          })
      if (!resp.ok) {
        let detail = `Save failed (${resp.status})`
        try {
          const j = await resp.json()
          if (j?.detail) detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)
        } catch { /* ignore */ }
        throw new Error(detail)
      }
      await onSaved()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }, [isCreate, template.id, name, displayName, description, configText,
      requiredEnvText, supportedEngines, onSaved])

  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose() }}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>
            {isCreate ? 'New MCP template' : `Edit ${template.name}`}
          </DialogTitle>
          <DialogDescription>
            Define a custom MCP server. The <code>config_per_engine</code> JSON
            is the engine-native body (<code>mcpServers.&lt;name&gt;</code> for
            Claude / Gemini, <code>[mcp_servers.&lt;name&gt;]</code> for Codex).
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3 py-2 max-h-[60vh] overflow-auto">
          <div>
            <Label htmlFor="mcp-name">Name (slug)</Label>
            <Input
              id="mcp-name"
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="internal-kb"
              disabled={!isCreate}
            />
          </div>
          <div>
            <Label htmlFor="mcp-display">Display name</Label>
            <Input
              id="mcp-display"
              value={displayName}
              onChange={e => setDisplayName(e.target.value)}
              placeholder="Internal Knowledge Base"
            />
          </div>
          <div>
            <Label htmlFor="mcp-desc">Description</Label>
            <Input
              id="mcp-desc"
              value={description}
              onChange={e => setDescription(e.target.value)}
            />
          </div>
          <div>
            <Label>Supported engines</Label>
            <div className="flex flex-wrap gap-2 pt-1">
              {SUPPORTED_ENGINES.map(engine => (
                <label
                  key={engine}
                  className="flex items-center gap-1.5 rounded-[var(--radius-sm)] border border-[rgba(0,0,0,0.1)] bg-white px-2 py-1 text-xs cursor-pointer"
                >
                  <input
                    type="checkbox"
                    checked={supportedEngines.has(engine)}
                    onChange={() => toggleEngine(engine)}
                  />
                  {engine}
                </label>
              ))}
            </div>
          </div>
          <div>
            <Label htmlFor="mcp-env">Required env vars (comma-separated)</Label>
            <Input
              id="mcp-env"
              value={requiredEnvText}
              onChange={e => setRequiredEnvText(e.target.value)}
              placeholder="API_KEY, ORG_ID"
            />
          </div>
          <div>
            <Label htmlFor="mcp-config">config_per_engine (JSON)</Label>
            <textarea
              id="mcp-config"
              value={configText}
              onChange={e => setConfigText(e.target.value)}
              className="w-full min-h-[200px] rounded-[var(--radius-sm)] border border-[rgba(0,0,0,0.1)] bg-white px-3 py-2 font-mono text-xs"
            />
          </div>
          {error && (
            <p className="text-xs text-[var(--color-danger)] whitespace-pre-wrap">{error}</p>
          )}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button
            onClick={() => void handleSave()}
            disabled={busy || !name.trim() || !displayName.trim()}
          >
            {busy ? 'Saving…' : isCreate ? 'Create' : 'Save'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

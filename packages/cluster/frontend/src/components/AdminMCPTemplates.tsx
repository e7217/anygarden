import { useCallback, useEffect, useMemo, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription,
} from '@/components/ui/dialog'
import { Plug, Plus, Trash2, RefreshCw, Link as LinkIcon, X } from 'lucide-react'
import { apiFetch } from '@/lib/api'
import { useAgents } from '@/hooks/useAgents'
import {
  slugify,
  extractPlaceholders,
  buildTemplatePayload,
  parseTemplateIntoForm,
  SUPPORTED_ENGINE_IDS,
  type TemplateFormState,
  type EnvRow,
} from '@/lib/mcpTemplateForm'

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

// ── Advanced fallback state ──────────────────────────────────────

interface AdvancedState {
  slug: string
  requiredEnvText: string
  supportedEngines: Set<EngineId>
  configText: string
}

function makeInitialSimpleForm(): TemplateFormState {
  return {
    slug: '',
    displayName: '',
    description: '',
    command: 'npx',
    args: [''],
    envRows: [],
  }
}

function makeAdvancedStateFromTemplate(template: Template): AdvancedState {
  return {
    slug: template.name,
    requiredEnvText: template.required_env_vars.join(', '),
    supportedEngines: new Set(template.supported_engines as EngineId[]),
    configText: JSON.stringify(
      template.config_per_engine && Object.keys(template.config_per_engine).length > 0
        ? template.config_per_engine
        : { 'claude-code': { command: 'npx', args: [], env: {} } },
      null,
      2,
    ),
  }
}

function makeAdvancedStateFromForm(form: TemplateFormState): AdvancedState {
  const payload = buildTemplatePayload(form)
  return {
    slug: form.slug,
    requiredEnvText: payload.required_env_vars.join(', '),
    supportedEngines: new Set(SUPPORTED_ENGINE_IDS as readonly EngineId[]),
    configText: JSON.stringify(payload.config_per_engine, null, 2),
  }
}

function CustomEditorDialog({ template, onClose, onSaved }: CustomEditorProps) {
  const isCreate = template.id === ''

  const initial = useMemo(() => {
    if (isCreate) {
      return {
        mode: 'simple' as const,
        form: { ...makeInitialSimpleForm(), displayName: template.display_name },
        advanced: undefined,
      }
    }
    const parsed = parseTemplateIntoForm({
      name: template.name,
      display_name: template.display_name,
      description: template.description,
      config_per_engine: template.config_per_engine,
      required_env_vars: template.required_env_vars,
      supported_engines: template.supported_engines,
    })
    if (parsed.mode === 'simple') {
      return { mode: 'simple' as const, form: parsed.state, advanced: undefined }
    }
    return {
      mode: 'advanced' as const,
      form: { ...makeInitialSimpleForm(), slug: template.name, displayName: template.display_name },
      advanced: makeAdvancedStateFromTemplate(template),
    }
  }, [isCreate, template])

  const [mode, setMode] = useState<'simple' | 'advanced'>(initial.mode)
  const [form, setForm] = useState<TemplateFormState>(initial.form)
  const [advanced, setAdvanced] = useState<AdvancedState>(
    initial.advanced ?? makeAdvancedStateFromTemplate(template),
  )
  const [slugTouched, setSlugTouched] = useState<boolean>(!isCreate)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Create 모드에서 display name 변경 시 slug 자동 유도 (사용자가 직접 편집한 적 없을 때만).
  useEffect(() => {
    if (!isCreate || slugTouched) return
    setForm(prev => ({ ...prev, slug: slugify(prev.displayName) }))
  }, [form.displayName, isCreate, slugTouched])

  const placeholders = useMemo(
    () => extractPlaceholders([
      ...form.args,
      Object.fromEntries(
        form.envRows
          .filter(r => r.key.trim())
          .map(r => [r.key.trim(), r.secret ? `\${${r.key.trim()}}` : r.value]),
      ),
    ]),
    [form.args, form.envRows],
  )

  const updateArg = (i: number, value: string) => {
    setForm(prev => ({ ...prev, args: prev.args.map((a, idx) => (idx === i ? value : a)) }))
  }
  const addArg = () => setForm(prev => ({ ...prev, args: [...prev.args, ''] }))
  const removeArg = (i: number) => {
    setForm(prev => ({ ...prev, args: prev.args.filter((_, idx) => idx !== i) }))
  }

  const updateEnv = (i: number, patch: Partial<EnvRow>) => {
    setForm(prev => ({
      ...prev,
      envRows: prev.envRows.map((r, idx) => (idx === i ? { ...r, ...patch } : r)),
    }))
  }
  const addEnv = () => {
    setForm(prev => ({ ...prev, envRows: [...prev.envRows, { key: '', secret: true, value: '' }] }))
  }
  const removeEnv = (i: number) => {
    setForm(prev => ({ ...prev, envRows: prev.envRows.filter((_, idx) => idx !== i) }))
  }

  const toggleAdvanced = () => {
    if (mode === 'simple') {
      // simple → advanced: 현재 form을 fan-out한 결과를 advanced state로 시드.
      setAdvanced(makeAdvancedStateFromForm(form))
      setMode('advanced')
    } else {
      // advanced → simple: parseTemplateIntoForm으로 복원 시도.
      try {
        const parsed = JSON.parse(advanced.configText) as Record<string, Record<string, unknown>>
        const out = parseTemplateIntoForm({
          name: advanced.slug,
          display_name: form.displayName,
          description: form.description,
          config_per_engine: parsed,
          required_env_vars: advanced.requiredEnvText
            .split(',')
            .map(s => s.trim())
            .filter(Boolean),
          supported_engines: Array.from(advanced.supportedEngines),
        })
        if (out.mode === 'advanced') {
          setError('Current advanced config cannot be represented in simple mode (engine divergence or non-stdio keys).')
          return
        }
        setForm(out.state)
        setError(null)
        setMode('simple')
      } catch (e) {
        setError(`config_per_engine is not valid JSON: ${(e as Error).message}`)
      }
    }
  }

  const toggleAdvancedEngine = (engine: EngineId) => {
    setAdvanced(prev => {
      const next = new Set(prev.supportedEngines)
      if (next.has(engine)) next.delete(engine)
      else next.add(engine)
      return { ...prev, supportedEngines: next }
    })
  }

  const saveOnce = useCallback(async (slugOverride: string): Promise<{ ok: true } | { ok: false; status: number; detail: string }> => {
    let body: Record<string, unknown>
    if (mode === 'simple') {
      const payload = buildTemplatePayload({ ...form, slug: slugOverride })
      body = {
        name: payload.name,
        display_name: payload.display_name,
        description: payload.description,
        icon: payload.icon,
        config_per_engine: payload.config_per_engine,
        required_env_vars: payload.required_env_vars,
        supported_engines: payload.supported_engines,
      }
    } else {
      let configParsed: Record<string, Record<string, unknown>>
      try {
        configParsed = JSON.parse(advanced.configText)
      } catch (e) {
        return { ok: false, status: 0, detail: `config_per_engine is not valid JSON: ${(e as Error).message}` }
      }
      body = {
        name: slugOverride,
        display_name: form.displayName.trim(),
        description: form.description.trim() || null,
        icon: null,
        config_per_engine: configParsed,
        required_env_vars: advanced.requiredEnvText.split(',').map(s => s.trim()).filter(Boolean),
        supported_engines: Array.from(advanced.supportedEngines),
      }
    }
    const resp = isCreate
      ? await apiFetch('/api/v1/admin/mcp-templates', {
          method: 'POST',
          body: JSON.stringify(body),
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
    if (resp.ok) return { ok: true }
    let detail = `Save failed (${resp.status})`
    try {
      const j = await resp.json()
      if (j?.detail) detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)
    } catch { /* ignore */ }
    return { ok: false, status: resp.status, detail }
  }, [mode, form, advanced, isCreate, template.id])

  const handleSave = useCallback(async () => {
    setBusy(true)
    setError(null)
    try {
      const initialSlug = (mode === 'simple' ? form.slug : advanced.slug).trim()
      if (!initialSlug) {
        setError('Slug cannot be empty.')
        return
      }
      if (!form.displayName.trim()) {
        setError('Display name cannot be empty.')
        return
      }
      // Create 모드에서만 slug 충돌 시 suffix 재시도. Edit 모드는 slug 불변.
      const maxAttempts = isCreate ? 3 : 1
      let candidate = initialSlug
      for (let attempt = 1; attempt <= maxAttempts; attempt++) {
        const result = await saveOnce(candidate)
        if (result.ok) {
          await onSaved()
          return
        }
        if (result.status === 409 && attempt < maxAttempts && isCreate) {
          candidate = `${initialSlug}-${attempt + 1}`
          continue
        }
        setError(result.detail + (result.status === 409
          ? ' (Try a different display name or set a custom slug in Advanced mode.)'
          : ''))
        return
      }
    } finally {
      setBusy(false)
    }
  }, [mode, form.slug, form.displayName, advanced.slug, isCreate, saveOnce, onSaved])

  const saveDisabled = busy
    || !form.displayName.trim()
    || (mode === 'simple' ? !form.slug.trim() || !form.command.trim() : !advanced.slug.trim())

  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose() }}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>
            {isCreate ? 'New MCP template' : `Edit ${template.name}`}
          </DialogTitle>
          <DialogDescription>
            Register a stdio MCP server. Use <code>${'${VAR}'}</code> in args or
            env values for placeholders that admins will fill in per agent.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 py-2 max-h-[60vh] overflow-y-auto">
          {mode === 'simple' ? (
            <>
              <div>
                <Label htmlFor="mcp-display">Display name</Label>
                <Input
                  id="mcp-display"
                  value={form.displayName}
                  onChange={e => setForm(prev => ({ ...prev, displayName: e.target.value }))}
                  placeholder="Internal Knowledge Base"
                />
              </div>
              <div>
                <Label htmlFor="mcp-slug">
                  Slug
                  {isCreate && !slugTouched && (
                    <span className="ml-1 text-[10px] font-normal text-[var(--color-foreground-subtle)]">
                      (auto)
                    </span>
                  )}
                </Label>
                <Input
                  id="mcp-slug"
                  value={form.slug}
                  onChange={e => {
                    setSlugTouched(true)
                    setForm(prev => ({ ...prev, slug: e.target.value }))
                  }}
                  placeholder="internal-knowledge-base"
                  disabled={!isCreate}
                />
              </div>
              <div>
                <Label htmlFor="mcp-desc">Description</Label>
                <Input
                  id="mcp-desc"
                  value={form.description}
                  onChange={e => setForm(prev => ({ ...prev, description: e.target.value }))}
                />
              </div>
              <div>
                <Label>Transport</Label>
                <div className="mt-1 inline-flex rounded-[var(--radius-sm)] border border-[rgba(0,0,0,0.1)] bg-[var(--color-background)] px-2 py-1 text-xs text-[var(--color-foreground-muted)]">
                  stdio
                </div>
              </div>
              <div>
                <Label htmlFor="mcp-command">Command</Label>
                <Input
                  id="mcp-command"
                  value={form.command}
                  onChange={e => setForm(prev => ({ ...prev, command: e.target.value }))}
                  placeholder="npx"
                />
              </div>
              <div>
                <Label>Args</Label>
                <div className="space-y-1.5 pt-1">
                  {form.args.map((arg, i) => (
                    <div key={i} className="flex items-center gap-2">
                      <Input
                        value={arg}
                        onChange={e => updateArg(i, e.target.value)}
                        placeholder={i === 0 ? '-y' : '@modelcontextprotocol/server-*'}
                        className="flex-1"
                      />
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => removeArg(i)}
                        aria-label={`Remove arg ${i + 1}`}
                      >
                        <X className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  ))}
                  <Button variant="ghost" size="sm" onClick={addArg}>
                    <Plus className="mr-1 h-3.5 w-3.5" /> Add arg
                  </Button>
                </div>
              </div>
              <div>
                <Label>Environment variables</Label>
                <div className="space-y-1.5 pt-1">
                  {form.envRows.map((row, i) => (
                    <div key={i} className="flex items-center gap-2">
                      <Input
                        value={row.key}
                        onChange={e => updateEnv(i, { key: e.target.value })}
                        placeholder="GITHUB_TOKEN"
                        className="flex-1 font-mono text-xs"
                      />
                      <label className="flex shrink-0 items-center gap-1 text-xs text-[var(--color-foreground-muted)]">
                        <input
                          type="checkbox"
                          checked={row.secret}
                          onChange={e => updateEnv(i, { secret: e.target.checked })}
                        />
                        Secret
                      </label>
                      {!row.secret && (
                        <Input
                          value={row.value}
                          onChange={e => updateEnv(i, { value: e.target.value })}
                          placeholder="value"
                          className="flex-1 text-xs"
                        />
                      )}
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => removeEnv(i)}
                        aria-label={`Remove env ${i + 1}`}
                      >
                        <X className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  ))}
                  <Button variant="ghost" size="sm" onClick={addEnv}>
                    <Plus className="mr-1 h-3.5 w-3.5" /> Add env
                  </Button>
                </div>
              </div>
              <div>
                <Label className="text-[var(--color-foreground-muted)]">
                  Required placeholders (auto)
                </Label>
                <div className="mt-1 flex flex-wrap gap-1">
                  {placeholders.length === 0 ? (
                    <span className="text-xs text-[var(--color-foreground-subtle)]">
                      None detected.
                    </span>
                  ) : (
                    placeholders.map(p => (
                      <Badge key={p} variant="outline" className="font-mono text-[10px]">
                        {p}
                      </Badge>
                    ))
                  )}
                </div>
              </div>
            </>
          ) : (
            <>
              <div>
                <Label htmlFor="mcp-slug-adv">Slug</Label>
                <Input
                  id="mcp-slug-adv"
                  value={advanced.slug}
                  onChange={e => setAdvanced(prev => ({ ...prev, slug: e.target.value }))}
                  placeholder="internal-kb"
                  disabled={!isCreate}
                />
              </div>
              <div>
                <Label htmlFor="mcp-display-adv">Display name</Label>
                <Input
                  id="mcp-display-adv"
                  value={form.displayName}
                  onChange={e => setForm(prev => ({ ...prev, displayName: e.target.value }))}
                />
              </div>
              <div>
                <Label htmlFor="mcp-desc-adv">Description</Label>
                <Input
                  id="mcp-desc-adv"
                  value={form.description}
                  onChange={e => setForm(prev => ({ ...prev, description: e.target.value }))}
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
                        checked={advanced.supportedEngines.has(engine)}
                        onChange={() => toggleAdvancedEngine(engine)}
                      />
                      {engine}
                    </label>
                  ))}
                </div>
              </div>
              <div>
                <Label htmlFor="mcp-env-adv">Required env vars (comma-separated)</Label>
                <Input
                  id="mcp-env-adv"
                  value={advanced.requiredEnvText}
                  onChange={e => setAdvanced(prev => ({ ...prev, requiredEnvText: e.target.value }))}
                  placeholder="API_KEY, ORG_ID"
                />
              </div>
              <div>
                <Label htmlFor="mcp-config-adv">config_per_engine (JSON)</Label>
                <textarea
                  id="mcp-config-adv"
                  value={advanced.configText}
                  onChange={e => setAdvanced(prev => ({ ...prev, configText: e.target.value }))}
                  className="w-full min-h-[200px] rounded-[var(--radius-sm)] border border-[rgba(0,0,0,0.1)] bg-white px-3 py-2 font-mono text-xs"
                />
              </div>
            </>
          )}
        </div>

        {error && (
          <p className="px-1 pb-2 text-xs text-[var(--color-danger)] whitespace-pre-wrap">
            {error}
          </p>
        )}

        <DialogFooter>
          <Button
            variant="ghost"
            size="sm"
            onClick={toggleAdvanced}
            className="mr-auto text-[var(--color-foreground-muted)]"
          >
            {mode === 'simple' ? 'Advanced ▸' : '◂ Simple'}
          </Button>
          <Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button onClick={() => void handleSave()} disabled={saveDisabled}>
            {busy ? 'Saving…' : isCreate ? 'Create' : 'Save'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

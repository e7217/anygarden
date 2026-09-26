import { useCallback, useEffect, useMemo, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription,
} from '@/components/ui/dialog'
import { Plug, Plus, Trash2, RefreshCw, Link as LinkIcon, X, Eye, EyeOff } from 'lucide-react'
import { apiFetch } from '@/lib/api'
import { useAgents } from '@/hooks/useAgents'
import { useLocale } from '@/i18n/LocaleProvider'
import { useFeedback } from '@/components/feedback/FeedbackProvider'
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

const SUPPORTED_ENGINES = SUPPORTED_ENGINE_IDS
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
  const { t } = useLocale()
  const { confirm: confirmAction } = useFeedback()
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
      if (!resp.ok) throw new Error(t('admin.mcp.loadFailed', { status: resp.status }))
      setTemplates(await resp.json())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [t])

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
    if (!await confirmAction({ title: t('admin.mcp.deleteTitle'), description: t('admin.mcp.confirmDelete', { name: template.display_name }), destructive: true })) return
    const resp = await apiFetch(`/api/v1/admin/mcp-templates/${template.id}`, {
      method: 'DELETE',
    })
    if (resp.status === 204) {
      await load()
    } else {
      let detail = t('admin.mcp.deleteFailed', { status: resp.status })
      try {
        const body = await resp.json()
        if (body?.detail) detail = body.detail
      } catch { /* ignore */ }
      setError(detail)
    }
  }, [load, t, confirmAction])

  return (
    <div className="mx-auto w-full max-w-4xl space-y-6 px-4 py-5 sm:px-6 sm:py-6">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <h1 className="text-heading text-[var(--color-foreground)]">{t('admin.mcp.title')}</h1>
          <p className="text-sm text-[var(--color-foreground-muted)]">
            {t('admin.mcp.description')}
          </p>
        </div>
        <div className="flex w-full flex-wrap items-center gap-2 lg:w-auto lg:shrink-0 lg:justify-end">
          <Button variant="ghost" size="sm" onClick={() => void load()} disabled={loading}>
            <RefreshCw className={`mr-1 h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
            {t('common.refresh')}
          </Button>
          <Button size="sm" className="order-first w-full sm:w-auto lg:order-last" onClick={() => setEditorTarget({
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
            <Plus className="mr-1 h-3.5 w-3.5" /> {t('admin.mcp.newTemplate')}
          </Button>
        </div>
      </div>

      {error && (
        <div className="rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-[color:color-mix(in_srgb,var(--color-danger)_8%,transparent)] p-3 text-sm text-[var(--color-danger)]">
          {error}
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-1 rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-[var(--color-surface-elevated)] p-1 w-fit">
        {(['builtin', 'custom'] as Tab[]).map(tab => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            aria-pressed={activeTab === tab}
            className={`h-[var(--control-sm-height)] rounded-[var(--radius-sm)] px-3 text-sm font-medium transition-colors ${
              activeTab === tab
                ? 'bg-[var(--color-background)] text-[var(--color-foreground)]'
                : 'text-[var(--color-foreground-muted)] hover:text-[var(--color-foreground)]'
            }`}
          >
            {tab === 'builtin' ? t('admin.mcp.builtin') : t('admin.mcp.custom')}
          </button>
        ))}
      </div>

      {visibleTemplates.length === 0 && !loading ? (
        <div className="rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-6 py-10 text-center shadow-[var(--shadow-card)]">
          <Plug
            className="mx-auto mb-3 h-8 w-8 text-[var(--color-foreground-subtle)]"
            strokeWidth={1.5}
          />
          <p className="text-sm text-[var(--color-foreground-muted)]">
            {activeTab === 'builtin'
              ? t('admin.mcp.noBuiltin')
              : t('admin.mcp.noCustom')}
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {visibleTemplates.map(template => (
            <div
              key={template.id}
              className="rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-4 py-3 shadow-[var(--shadow-card)]"
              data-testid={`mcp-template-row-${template.id}`}
            >
              <div className="flex min-w-0 flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                <div className="min-w-0 flex-1">
                  <div className="flex min-w-0 flex-wrap items-center gap-2">
                    <h3 className="text-sm font-semibold text-[var(--color-foreground)]">
                      {template.display_name}
                    </h3>
                    <Badge variant="outline">
                      <code className="text-[11px]">{template.name}</code>
                    </Badge>
                    {template.source === 'builtin' && (
                      <Badge variant="outline" className="text-[var(--color-foreground-muted)]">
                        {t('admin.mcp.builtin')}
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
                        {t('admin.mcp.envCount', { count: template.required_env_vars.length })}
                      </Badge>
                    )}
                  </div>
                  <p className="mt-2 text-xs text-[var(--color-foreground-muted)]">
                    {t('admin.mcp.instanceCount', { count: template.instance_count })}
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-1 border-t border-[var(--color-border)] pt-2 [&_button]:lg:shrink-0 lg:border-t-0 lg:pt-0 lg:[&_button]:min-h-0">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setAttachTarget(template)}
                    data-testid={`mcp-template-attach-${template.id}`}
                  >
                    <LinkIcon className="mr-1 h-3.5 w-3.5" />
                    {t('admin.mcp.attach')}
                  </Button>
                  {template.source === 'custom' && (
                    <>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setEditorTarget(template)}
                      >
                        {t('admin.mcp.edit')}
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => void handleDelete(template)}
                        aria-label={t('admin.mcp.deleteLabel', { name: template.display_name })}
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
  const { t } = useLocale()
  const eligibleAgents = agents.filter(a => template.supported_engines.includes(a.engine))
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(
    eligibleAgents[0]?.id ?? null,
  )
  const [envValues, setEnvValues] = useState<Record<string, string>>({})
  const [showValues, setShowValues] = useState<Record<string, boolean>>({})
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
        let detail = t('admin.mcp.attachFailed', { status: resp.status })
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
  }, [selectedAgentId, template.id, envValues, onClose, t])

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
            {t('admin.mcp.attachTitle', { name: template.name })}
          </DialogTitle>
          <DialogDescription>
            {t('admin.mcp.attachDescription')}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3 py-2">
          {eligibleAgents.length === 0 ? (
            <p className="text-sm text-[var(--color-foreground-muted)]">
              {t('admin.mcp.noEligibleAgents', { engines: template.supported_engines.join(', ') })}
            </p>
          ) : (
            <>
              <div>
                <Label htmlFor="mcp-attach-agent">{t('admin.mcp.agent')}</Label>
                <select
                  id="mcp-attach-agent"
                  value={selectedAgentId ?? ''}
                  onChange={(e) => {
                    setSelectedAgentId(e.target.value)
                    setEnvValues({})
                  }}
                  className="w-full rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-2 py-1.5 text-sm"
                >
                  {eligibleAgents.map(a => (
                    <option key={a.id} value={a.id}>
                      {a.name} ({a.engine})
                    </option>
                  ))}
                </select>
              </div>

              {existing && (
                <div className="rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-[var(--color-background)] px-3 py-2 text-xs text-[var(--color-foreground-muted)]">
                  {t('admin.mcp.alreadyAttached')}
                </div>
              )}

              {template.required_env_vars.map(varName => {
                const visible = showValues[varName] ?? false
                return (
                  <div key={varName}>
                    <Label htmlFor={`mcp-env-${varName}`}>{varName}</Label>
                    <div className="relative">
                      <Input
                        id={`mcp-env-${varName}`}
                        type={visible ? 'text' : 'password'}
                        autoComplete="off"
                        className="pr-9 font-mono"
                        value={envValues[varName] ?? ''}
                        onChange={(e) => setEnvValues(v => ({ ...v, [varName]: e.target.value }))}
                      />
                      <button
                        type="button"
                        onClick={() => setShowValues(v => ({ ...v, [varName]: !visible }))}
                        aria-label={visible ? t('admin.mcp.hideVariable', { name: varName }) : t('admin.mcp.showVariable', { name: varName })}
                        aria-pressed={visible}
                        className="absolute right-0 top-0 flex h-full w-9 items-center justify-center text-[var(--color-foreground-subtle)] transition-colors hover:text-[var(--color-foreground)] focus-visible:outline-none focus-visible:text-[var(--color-foreground)]"
                      >
                        {visible
                          ? <EyeOff className="h-3.5 w-3.5" aria-hidden="true" />
                          : <Eye className="h-3.5 w-3.5" aria-hidden="true" />}
                      </button>
                    </div>
                  </div>
                )
              })}

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
              {t('admin.mcp.detach')}
            </Button>
          )}
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            {t('common.cancel')}
          </Button>
          <Button
            onClick={() => void handleAttach()}
            disabled={
              busy || !selectedAgentId
              || template.required_env_vars.some(v => !envValues[v])
            }
          >
            {busy ? t('admin.mcp.saving') : existing ? t('admin.mcp.update') : t('admin.mcp.attach')}
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
        : { 'codex-cli': { command: 'npx', args: [], env: {} } },
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
  const { t } = useLocale()
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
          setError(t('admin.mcp.simpleConversionError'))
          return
        }
        setForm(out.state)
        setError(null)
        setMode('simple')
      } catch (e) {
        setError(t('admin.mcp.invalidJson', { error: (e as Error).message }))
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
        return { ok: false, status: 0, detail: t('admin.mcp.invalidJson', { error: (e as Error).message }) }
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
    let detail = t('admin.mcp.saveFailed', { status: resp.status })
    try {
      const j = await resp.json()
      if (j?.detail) detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)
    } catch { /* ignore */ }
    return { ok: false, status: resp.status, detail }
  }, [mode, form, advanced, isCreate, template.id, t])

  const handleSave = useCallback(async () => {
    setBusy(true)
    setError(null)
    try {
      const initialSlug = (mode === 'simple' ? form.slug : advanced.slug).trim()
      if (!initialSlug) {
        setError(t('admin.mcp.emptySlug'))
        return
      }
      if (!form.displayName.trim()) {
        setError(t('admin.mcp.emptyDisplayName'))
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
          ? ` (${t('admin.mcp.slugConflict')})`
          : ''))
        return
      }
    } finally {
      setBusy(false)
    }
  }, [mode, form.slug, form.displayName, advanced.slug, isCreate, saveOnce, onSaved, t])

  const saveDisabled = busy
    || !form.displayName.trim()
    || (mode === 'simple' ? !form.slug.trim() || !form.command.trim() : !advanced.slug.trim())

  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose() }}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>
            {isCreate ? t('admin.mcp.newTitle') : t('admin.mcp.editTitle', { name: template.name })}
          </DialogTitle>
          <DialogDescription>
            {t('admin.mcp.editorDescription')}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 py-2 px-1 max-h-[60vh] overflow-y-auto">
          {mode === 'simple' ? (
            <>
              <div>
                <Label htmlFor="mcp-display">{t('admin.mcp.displayName')}</Label>
                <Input
                  id="mcp-display"
                  value={form.displayName}
                  onChange={e => setForm(prev => ({ ...prev, displayName: e.target.value }))}
                  placeholder={t('admin.mcp.displayNamePlaceholder')}
                />
              </div>
              <div>
                <Label htmlFor="mcp-slug">
                  {t('admin.mcp.slug')}
                  {isCreate && !slugTouched && (
                    <span className="ml-1 text-[10px] font-normal text-[var(--color-foreground-subtle)]">
                      {t('admin.mcp.auto')}
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
                <Label htmlFor="mcp-desc">{t('admin.mcp.descriptionLabel')}</Label>
                <Input
                  id="mcp-desc"
                  value={form.description}
                  onChange={e => setForm(prev => ({ ...prev, description: e.target.value }))}
                />
              </div>
              <div>
                <Label>{t('admin.mcp.transport')}</Label>
                <div className="mt-1 inline-flex rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-[var(--color-background)] px-2 py-1 text-xs text-[var(--color-foreground-muted)]">
                  stdio
                </div>
              </div>
              <div>
                <Label htmlFor="mcp-command">{t('admin.mcp.command')}</Label>
                <Input
                  id="mcp-command"
                  value={form.command}
                  onChange={e => setForm(prev => ({ ...prev, command: e.target.value }))}
                  placeholder="npx"
                />
              </div>
              <div>
                <Label>{t('admin.mcp.args')}</Label>
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
                        aria-label={t('admin.mcp.removeArg', { count: i + 1 })}
                      >
                        <X className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  ))}
                  <Button variant="ghost" size="sm" onClick={addArg}>
                    <Plus className="mr-1 h-3.5 w-3.5" /> {t('admin.mcp.addArg')}
                  </Button>
                </div>
              </div>
              <div>
                <Label>{t('admin.mcp.envVariables')}</Label>
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
                        {t('admin.mcp.secret')}
                      </label>
                      {!row.secret && (
                        <Input
                          value={row.value}
                          onChange={e => updateEnv(i, { value: e.target.value })}
                          placeholder={t('admin.mcp.valuePlaceholder')}
                          className="flex-1 text-xs"
                        />
                      )}
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => removeEnv(i)}
                        aria-label={t('admin.mcp.removeEnv', { count: i + 1 })}
                      >
                        <X className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  ))}
                  <Button variant="ghost" size="sm" onClick={addEnv}>
                    <Plus className="mr-1 h-3.5 w-3.5" /> {t('admin.mcp.addEnv')}
                  </Button>
                </div>
              </div>
              <div>
                <Label className="text-[var(--color-foreground-muted)]">
                  {t('admin.mcp.placeholders')}
                </Label>
                <div className="mt-1 flex flex-wrap gap-1">
                  {placeholders.length === 0 ? (
                    <span className="text-xs text-[var(--color-foreground-subtle)]">
                      {t('admin.mcp.noneDetected')}
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
                <Label htmlFor="mcp-slug-adv">{t('admin.mcp.slug')}</Label>
                <Input
                  id="mcp-slug-adv"
                  value={advanced.slug}
                  onChange={e => setAdvanced(prev => ({ ...prev, slug: e.target.value }))}
                  placeholder="internal-kb"
                  disabled={!isCreate}
                />
              </div>
              <div>
                <Label htmlFor="mcp-display-adv">{t('admin.mcp.displayName')}</Label>
                <Input
                  id="mcp-display-adv"
                  value={form.displayName}
                  onChange={e => setForm(prev => ({ ...prev, displayName: e.target.value }))}
                />
              </div>
              <div>
                <Label htmlFor="mcp-desc-adv">{t('admin.mcp.descriptionLabel')}</Label>
                <Input
                  id="mcp-desc-adv"
                  value={form.description}
                  onChange={e => setForm(prev => ({ ...prev, description: e.target.value }))}
                />
              </div>
              <div>
                <Label>{t('admin.mcp.supportedEngines')}</Label>
                <div className="flex flex-wrap gap-2 pt-1">
                  {SUPPORTED_ENGINES.map(engine => (
                    <label
                      key={engine}
                      className="flex items-center gap-1.5 rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-2 py-1 text-xs cursor-pointer"
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
                <Label htmlFor="mcp-env-adv">{t('admin.mcp.requiredEnvVars')}</Label>
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
                  className="w-full min-h-[200px] rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-3 py-2 font-mono text-xs"
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
            {mode === 'simple' ? t('admin.mcp.advanced') : t('admin.mcp.simple')}
          </Button>
          <Button variant="ghost" onClick={onClose} disabled={busy}>{t('common.cancel')}</Button>
          <Button onClick={() => void handleSave()} disabled={saveDisabled}>
            {busy ? t('admin.mcp.saving') : isCreate ? t('common.create') : t('common.save')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

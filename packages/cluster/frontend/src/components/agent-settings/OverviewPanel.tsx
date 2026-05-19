/**
 * OverviewPanel — agent identity and quick-glance metadata (#158).
 *
 * First section of the unified AgentSettingsDialog. Consolidates
 * information the admin previously had to hunt for across three
 * dialogs + one broken menu item:
 *
 * - Avatar — click to expand the emoji/icon picker inline.
 * - Name  — click-to-edit with blur-commit (persists via
 *   ``updateAgent({name})``).
 * - ID    — displayed as selectable text + Copy button with
 *   "Copied" feedback. Replaces the previous menu-level
 *   ``onCopyId`` handler, which silently swallowed failures in
 *   insecure contexts.
 * - Engine, State — read-only.
 *
 * The Copy ID button falls back gracefully when the clipboard API
 * rejects (insecure context, denied permissions): it selects the
 * ID text so the admin can copy it manually, and shows "Clipboard
 * unavailable" in place of "Copied".
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Copy, Check, AlertCircle } from 'lucide-react'
import { EntityAvatar, type AvatarKind } from '@/components/EntityAvatar'
import PresenceDot from '@/components/PresenceDot'
import { agentStatusLabel, deriveAgentOnline } from '@/lib/agent-liveness'
import type { Agent, EngineCatalog } from '@/hooks/useAgents'
import AvatarPickerPanel from '@/components/agent-settings/AvatarPickerPanel'

type CopyState = 'idle' | 'ok' | 'fallback' | 'error'
// ``loading`` while the catalog fetch is in flight, ``unavailable``
// once it resolves with ``null`` (engine not in the static catalog or
// fetch errored) — lets us hide the dropdowns without flashing an
// empty ``<select>``.
type CatalogState =
  | { kind: 'loading' }
  | { kind: 'ready'; catalog: EngineCatalog }
  | { kind: 'unavailable' }

// Match AdminMachines.tsx so the two dialogs render identical selects.
const SELECT_CSS =
  'flex h-9 w-full rounded-[var(--radius-xs)] border border-[var(--color-border-strong)] bg-[var(--color-background)] px-3 py-1 text-sm text-[var(--color-foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-brand-focus)] disabled:opacity-60'

// The `Agent.avatar_kind` column is typed as a loose `string` (it's
// open-ended at the DB layer), but EntityAvatar only understands
// `'emoji' | 'lucide'`. Unknown / missing values fall back to the
// seed-driven initial.
function narrowAvatarKind(
  raw: string | null | undefined,
): AvatarKind | null | undefined {
  if (raw === 'emoji' || raw === 'lucide') return raw
  return null
}

interface Props {
  agent: Agent | null
  updateAgent: (
    id: string,
    patch: {
      name?: string
      avatar_kind?: string | null
      avatar_kind_set?: boolean
      avatar_value?: string | null
      avatar_value_set?: boolean
      model?: string | null
      model_set?: boolean
      reasoning_effort?: string | null
      reasoning_effort_set?: boolean
      permission_level?: string | null
      permission_level_set?: boolean
      description?: string | null
      description_set?: boolean
      collaboration_mode?: 'solo' | 'collaborative'
      collaboration_mode_set?: boolean
    },
  ) => Promise<Agent>
  /** Issue #217 — populate the Model / Reasoning dropdowns. Optional
   *  so existing tests that don't care about config editing keep
   *  passing; when absent, the rows render in read-only fallback. */
  fetchEngineCatalog?: (engine: string) => Promise<EngineCatalog | null>
}

export default function OverviewPanel({ agent, updateAgent, fetchEngineCatalog }: Props) {
  const [showPicker, setShowPicker] = useState(false)
  const [nameDraft, setNameDraft] = useState(agent?.name ?? '')
  const [nameSaving, setNameSaving] = useState(false)
  const [nameError, setNameError] = useState<string | null>(null)
  // Issue #271 — public-facing self-introduction. Same blur-commit
  // pattern as ``nameDraft`` but with a 200-char cap (mirrors the
  // server-side ``Field(max_length=200)``).
  const [descriptionDraft, setDescriptionDraft] = useState(agent?.description ?? '')
  const [descriptionSaving, setDescriptionSaving] = useState(false)
  const [descriptionError, setDescriptionError] = useState<string | null>(null)
  const [copyState, setCopyState] = useState<CopyState>('idle')
  const idRef = useRef<HTMLSpanElement>(null)

  // #217 — engine config editing. Catalog is fetched per engine on
  // mount; ``configSaving`` gates both <select>s during an in-flight
  // updateAgent so a fat-fingered double-click can't race two PUTs.
  const [catalogState, setCatalogState] = useState<CatalogState>({ kind: 'loading' })
  const [configSaving, setConfigSaving] = useState(false)
  const [configError, setConfigError] = useState<string | null>(null)

  // Re-seed the name draft whenever the target agent changes so the
  // input reflects the new agent's current name.
  useEffect(() => {
    setNameDraft(agent?.name ?? '')
    setNameError(null)
  }, [agent?.id, agent?.name])

  // Mirror the name-draft re-seeding for description.
  useEffect(() => {
    setDescriptionDraft(agent?.description ?? '')
    setDescriptionError(null)
  }, [agent?.id, agent?.description])

  // Auto-clear copy feedback after 2 seconds. Depends on the
  // transitioning state so each new click resets the timer.
  useEffect(() => {
    if (copyState === 'idle') return
    const t = setTimeout(() => setCopyState('idle'), 2000)
    return () => clearTimeout(t)
  }, [copyState])

  // #217 — catalog fetch runs whenever we switch to a different agent
  // OR its engine changes. ``cancelled`` guards against an out-of-order
  // resolve if the admin opens the dialog, closes it, and re-opens on
  // another engine before the first fetch resolved.
  const agentEngine = agent?.engine ?? null
  useEffect(() => {
    if (!agentEngine || !fetchEngineCatalog) {
      setCatalogState({ kind: 'unavailable' })
      return
    }
    let cancelled = false
    setCatalogState({ kind: 'loading' })
    setConfigError(null)
    fetchEngineCatalog(agentEngine)
      .then(cat => {
        if (cancelled) return
        setCatalogState(cat ? { kind: 'ready', catalog: cat } : { kind: 'unavailable' })
      })
      .catch(() => {
        if (!cancelled) setCatalogState({ kind: 'unavailable' })
      })
    return () => {
      cancelled = true
    }
  }, [agentEngine, fetchEngineCatalog])

  // Per-model reasoning narrowing, mirroring AdminMachines.tsx so the
  // create and edit dialogs agree on which effort levels apply to
  // which model. Engine-level list is the fallback.
  const currentModel = agent?.model ?? ''
  const reasoningLevels = useMemo<readonly string[]>(() => {
    if (catalogState.kind !== 'ready') return []
    const { catalog } = catalogState
    if (currentModel) {
      const m = catalog.models.find(x => x.id === currentModel)
      if (m && m.reasoning_levels.length > 0) return m.reasoning_levels
    }
    return catalog.reasoning_levels
  }, [catalogState, currentModel])

  if (!agent) {
    return (
      <div
        className="flex h-full items-center justify-center text-caption text-[var(--color-foreground-subtle)]"
        data-testid="overview-panel-empty"
      >
        No agent selected
      </div>
    )
  }

  const machineOffline = agent.machine_online === false
  const agentOnline = deriveAgentOnline(agent.actual_state, { machineOffline })
  const displayState = agentStatusLabel(agent.actual_state, { machineOffline })

  const commitName = async () => {
    const trimmed = nameDraft.trim()
    if (trimmed === agent.name || trimmed === '') {
      setNameDraft(agent.name)
      setNameError(null)
      return
    }
    setNameSaving(true)
    setNameError(null)
    try {
      await updateAgent(agent.id, { name: trimmed })
    } catch (e) {
      setNameError(e instanceof Error ? e.message : String(e))
      setNameDraft(agent.name) // rollback on failure
    }
    setNameSaving(false)
  }

  // #271 — description blur-commit. Empty input clears the column on
  // the server (description=null + description_set=true) so the admin
  // can remove an outdated introduction. Pre-trim so trailing
  // whitespace doesn't cause a no-op PUT loop.
  const commitDescription = async () => {
    const stored = agent.description ?? ''
    const trimmed = descriptionDraft.trim()
    if (trimmed === stored.trim()) {
      // No-op: avoid hitting the server when the user just blurred the
      // field without changing it.
      return
    }
    setDescriptionSaving(true)
    setDescriptionError(null)
    try {
      await updateAgent(agent.id, {
        description: trimmed === '' ? null : trimmed,
        description_set: true,
      })
    } catch (e) {
      setDescriptionError(e instanceof Error ? e.message : String(e))
      setDescriptionDraft(agent.description ?? '') // rollback on failure
    }
    setDescriptionSaving(false)
  }

  // #217 — onChange commits (avatar-picker pattern). Sending ``null``
  // for an empty selection asks the server to clear the column and
  // fall back to the adapter's built-in default. ``*_set: true`` is
  // required so an unrelated PUT doesn't wipe the field.
  const handleModelChange = async (raw: string) => {
    const nextVal = raw === '' ? null : raw
    if ((agent.model ?? null) === nextVal) return
    setConfigSaving(true)
    setConfigError(null)
    try {
      await updateAgent(agent.id, { model: nextVal, model_set: true })
    } catch (e) {
      setConfigError(e instanceof Error ? e.message : String(e))
    }
    setConfigSaving(false)
  }

  const handleReasoningChange = async (raw: string) => {
    const nextVal = raw === '' ? null : raw
    if ((agent.reasoning_effort ?? null) === nextVal) return
    setConfigSaving(true)
    setConfigError(null)
    try {
      await updateAgent(agent.id, {
        reasoning_effort: nextVal,
        reasoning_effort_set: true,
      })
    } catch (e) {
      setConfigError(e instanceof Error ? e.message : String(e))
    }
    setConfigSaving(false)
  }

  // #309 — permission tier is a small enum: ``restricted`` | ``standard``
  // | ``trusted`` (or null = adapter default = standard). Same blur-
  // commit shape as ``handleReasoningChange``; the API layer enforces
  // admin-only mutation, so this handler stays UI-side simple.
  const handlePermissionLevelChange = async (raw: string) => {
    const nextVal = raw === '' ? null : raw
    if ((agent.permission_level ?? null) === nextVal) return
    setConfigSaving(true)
    setConfigError(null)
    try {
      await updateAgent(agent.id, {
        permission_level: nextVal,
        permission_level_set: true,
      })
    } catch (e) {
      setConfigError(e instanceof Error ? e.message : String(e))
    }
    setConfigSaving(false)
  }

  // #279 — collaboration mode is a small enum: ``solo`` | ``collaborative``.
  // onChange commits immediately (same pattern as model/reasoning).
  // The ``*_set`` flag protects the value from being clobbered by an
  // unrelated PATCH that happens to carry ``collaboration_mode: undefined``.
  const handleCollaborationChange = async (raw: string) => {
    if (raw !== 'solo' && raw !== 'collaborative') return
    if ((agent.collaboration_mode ?? 'solo') === raw) return
    setConfigSaving(true)
    setConfigError(null)
    try {
      await updateAgent(agent.id, {
        collaboration_mode: raw,
        collaboration_mode_set: true,
      })
    } catch (e) {
      setConfigError(e instanceof Error ? e.message : String(e))
    }
    setConfigSaving(false)
  }

  const handleCopyId = async () => {
    try {
      await navigator.clipboard.writeText(agent.id)
      setCopyState('ok')
    } catch {
      // Fallback: select the ID span so the admin can ⌘/Ctrl+C.
      const el = idRef.current
      if (el && window.getSelection) {
        const sel = window.getSelection()
        const range = document.createRange()
        range.selectNodeContents(el)
        sel?.removeAllRanges()
        sel?.addRange(range)
      }
      setCopyState('fallback')
    }
  }

  return (
    <div className="space-y-5" data-testid="overview-panel">
      {/* Identity block — avatar + name */}
      <div className="flex items-start gap-4">
        <button
          type="button"
          onClick={() => setShowPicker(v => !v)}
          aria-expanded={showPicker}
          aria-label="Change avatar"
          data-testid="overview-avatar-trigger"
          className="rounded-full ring-offset-2 transition hover:opacity-80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-brand-focus)]"
        >
          <EntityAvatar
            id={agent.id}
            name={agent.name}
            kind="agent"
            engine={agent.engine}
            size="lg"
            avatarKind={narrowAvatarKind(agent.avatar_kind)}
            avatarValue={agent.avatar_value}
          />
        </button>

        <div className="flex-1 min-w-0 space-y-1">
          <Input
            value={nameDraft}
            onChange={e => setNameDraft(e.target.value)}
            onBlur={() => void commitName()}
            onKeyDown={e => {
              if (e.key === 'Enter') {
                e.preventDefault()
                ;(e.currentTarget as HTMLInputElement).blur()
              } else if (e.key === 'Escape') {
                setNameDraft(agent.name)
                ;(e.currentTarget as HTMLInputElement).blur()
              }
            }}
            disabled={nameSaving}
            aria-label="Agent name"
            data-testid="overview-name-input"
            className="text-base font-medium"
          />
          {nameError ? (
            <div
              className="flex items-center gap-1 text-xs text-[var(--color-warning)]"
              data-testid="overview-name-error"
            >
              <AlertCircle className="h-3 w-3" aria-hidden="true" />
              {nameError}
            </div>
          ) : null}

          {/* #271 — public-facing self-introduction. Sits with the name
              because it's the same identity layer (what *others* see
              when they look at this agent). Blur-commits like the name;
              200-char cap mirrors ``Field(max_length=200)`` on the
              server. Helper text follows DESIGN.md §3.3 secondary text
              tone. */}
          <textarea
            value={descriptionDraft}
            onChange={e => setDescriptionDraft(e.target.value)}
            onBlur={() => void commitDescription()}
            onKeyDown={e => {
              if (e.key === 'Escape') {
                setDescriptionDraft(agent.description ?? '')
                ;(e.currentTarget as HTMLTextAreaElement).blur()
              }
            }}
            disabled={descriptionSaving}
            maxLength={200}
            rows={2}
            placeholder="Short introduction shown to other agents and users"
            aria-label="Agent description"
            data-testid="overview-description-input"
            className="flex w-full resize-none rounded-[var(--radius-xs)] border border-[var(--color-border-strong)] bg-[var(--color-background)] px-3 py-2 text-sm text-[var(--color-foreground)] placeholder:text-[var(--color-foreground-subtle)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-brand-focus)] disabled:opacity-60"
          />
          <div className="flex items-center justify-between text-[11px] text-[var(--color-foreground-subtle)]">
            <span>Visible to other agents (LLM roster) and users (mention popover, participants list).</span>
            <span data-testid="overview-description-counter">{descriptionDraft.length}/200</span>
          </div>
          {descriptionError ? (
            <div
              className="flex items-center gap-1 text-xs text-[var(--color-warning)]"
              data-testid="overview-description-error"
            >
              <AlertCircle className="h-3 w-3" aria-hidden="true" />
              {descriptionError}
            </div>
          ) : null}
        </div>
      </div>

      {/* Inline avatar picker — only visible when the admin clicks
          the avatar. AvatarPickerPanel fires onDone on Save or
          Cancel; we collapse the picker afterwards. */}
      {showPicker ? (
        <AvatarPickerPanel
          agent={agent}
          updateAgent={updateAgent}
          onDone={() => setShowPicker(false)}
        />
      ) : null}

      {/* Metadata grid */}
      <dl className="grid grid-cols-[6rem_1fr] gap-x-4 gap-y-3 text-sm">
        <dt className="text-[var(--color-foreground-muted)]">ID</dt>
        <dd className="flex items-center gap-2 min-w-0">
          <span
            ref={idRef}
            className="font-mono text-xs text-[var(--color-foreground)] bg-[var(--color-surface-alt)] rounded-[var(--radius-xs)] border border-[var(--color-border)] px-2 py-1 truncate"
            data-testid="overview-id-text"
          >
            {agent.id}
          </span>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => void handleCopyId()}
            title="Copy agent ID"
            data-testid="overview-copy-id"
          >
            {copyState === 'ok' ? (
              <Check className="h-4 w-4 text-[var(--color-success)]" />
            ) : (
              <Copy className="h-4 w-4" />
            )}
          </Button>
          {copyState === 'ok' ? (
            <span
              className="text-xs text-[var(--color-success)]"
              data-testid="overview-copy-feedback"
            >
              Copied
            </span>
          ) : copyState === 'fallback' ? (
            <span
              className="text-xs text-[var(--color-foreground-muted)]"
              data-testid="overview-copy-feedback"
            >
              Clipboard unavailable — text selected
            </span>
          ) : null}
        </dd>

        <dt className="text-[var(--color-foreground-muted)]">Engine</dt>
        <dd className="text-[var(--color-foreground)]">{agent.engine}</dd>

        {/* #217 — Model + Reasoning editing. Rows only render when
            the catalog resolved successfully; unknown/loading engines
            fall back to the name-only metadata we had before. */}
        {catalogState.kind === 'ready' ? (
          <>
            <dt className="text-[var(--color-foreground-muted)]">Model</dt>
            <dd>
              <select
                value={agent.model ?? ''}
                onChange={e => void handleModelChange(e.target.value)}
                disabled={configSaving}
                aria-label="Agent model"
                data-testid="overview-model-select"
                className={SELECT_CSS}
              >
                <option value="">
                  Default ({catalogState.catalog.default_model})
                </option>
                {catalogState.catalog.models.map(m => (
                  <option key={m.id} value={m.id}>
                    {m.label}
                  </option>
                ))}
                {/* Preserve a legacy value that's no longer listed in
                    the catalog so admins can see what's actually stored
                    instead of the UI silently collapsing to "Default". */}
                {agent.model &&
                !catalogState.catalog.models.some(m => m.id === agent.model) ? (
                  <option value={agent.model} disabled>
                    Current: {agent.model} (no longer in catalog)
                  </option>
                ) : null}
              </select>
            </dd>

            <dt className="text-[var(--color-foreground-muted)]">Reasoning</dt>
            <dd>
              <select
                value={agent.reasoning_effort ?? ''}
                onChange={e => void handleReasoningChange(e.target.value)}
                disabled={configSaving || reasoningLevels.length === 0}
                aria-label="Reasoning effort"
                data-testid="overview-reasoning-select"
                className={SELECT_CSS}
              >
                <option value="">Default</option>
                {reasoningLevels.map(level => (
                  <option key={level} value={level}>
                    {level.charAt(0).toUpperCase() + level.slice(1)}
                  </option>
                ))}
                {agent.reasoning_effort &&
                !reasoningLevels.includes(agent.reasoning_effort) ? (
                  <option value={agent.reasoning_effort} disabled>
                    Current: {agent.reasoning_effort} (no longer in catalog)
                  </option>
                ) : null}
              </select>
              {configError ? (
                <div
                  className="mt-1 flex items-center gap-1 text-xs text-[var(--color-warning)]"
                  data-testid="overview-config-error"
                >
                  <AlertCircle className="h-3 w-3" aria-hidden="true" />
                  {configError}
                </div>
              ) : null}
            </dd>
          </>
        ) : null}

        {/* #309 — Permission tier. Sits next to Model / Reasoning so
            it groups with the other adapter-spawn parameters. The
            REST endpoint is admin-only so non-admin users get a 403
            on PATCH; UI gating is "best effort" — we still render
            the select so non-admins see the current value, but the
            change is rejected at the API. ``trusted`` carries an
            inline ⚠ to flag host access. */}
        <dt className="text-[var(--color-foreground-muted)]">Permission</dt>
        <dd>
          <select
            value={agent.permission_level ?? ''}
            onChange={e => void handlePermissionLevelChange(e.target.value)}
            disabled={configSaving}
            aria-label="Agent permission tier"
            data-testid="overview-permission-select"
            className={SELECT_CSS}
          >
            <option value="">Default (standard)</option>
            <option value="restricted">Restricted — read-only</option>
            <option value="standard">Standard — workspace only</option>
            <option value="trusted">⚠ Trusted — host access</option>
          </select>
          {agent.permission_level === 'trusted' ? (
            <p
              className="mt-1 text-[11px] text-[var(--color-foreground-muted)]"
              data-testid="overview-permission-trusted-warning"
            >
              호스트 정보·명령 접근 가능. 신중히 사용하세요.
            </p>
          ) : null}
        </dd>

        {/* #279 — Collaboration policy. ``solo`` (default) keeps the
            agent answering within its own turn; ``collaborative``
            tells the agent to peer-mention teammates and synthesize
            their replies. The hint paragraph is appended to the LLM
            system prompt by the agent SDK on the next welcome / turn,
            so the toggle takes effect without a respawn. */}
        <dt className="text-[var(--color-foreground-muted)]">Collaboration</dt>
        <dd>
          <select
            value={agent.collaboration_mode ?? 'solo'}
            onChange={e => void handleCollaborationChange(e.target.value)}
            disabled={configSaving}
            aria-label="Agent collaboration mode"
            data-testid="overview-collaboration-select"
            className={SELECT_CSS}
          >
            <option value="solo">Solo — answer within own turn</option>
            <option value="collaborative">
              Collaborative — peer-mention teammates and synthesize
            </option>
          </select>
          <div className="mt-1 text-[11px] text-[var(--color-foreground-subtle)]">
            Recommend keeping at most one or two collaborative agents per room
            so they don't peer-ping each other in a loop.
          </div>
        </dd>

        <dt className="text-[var(--color-foreground-muted)]">State</dt>
        <dd className="flex items-center gap-2">
          <PresenceDot
            variant="agent"
            online={agentOnline}
            agentState={displayState}
          />
          <span className="text-[var(--color-foreground)]">
            {displayState}
          </span>
        </dd>
      </dl>
    </div>
  )
}

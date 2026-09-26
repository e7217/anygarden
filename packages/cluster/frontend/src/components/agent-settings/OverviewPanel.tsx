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
import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { Copy, Check, AlertCircle } from 'lucide-react'
import { EntityAvatar, type AvatarKind } from '@/components/EntityAvatar'
import PresenceDot from '@/components/PresenceDot'
import { agentStatusLabel, deriveAgentOnline } from '@/lib/agent-liveness'
import type { Agent, EngineCatalog } from '@/hooks/useAgents'
import type { ConnectionState } from '@/components/agent-settings/ModelConnectionPanel'
import AvatarPickerPanel from '@/components/agent-settings/AvatarPickerPanel'
import { useLocale } from '@/i18n/LocaleProvider'

type CopyState = 'idle' | 'ok' | 'fallback' | 'error'

function connectionSummary(agent: Agent, state: ConnectionState | null | undefined, defaultModel: string | undefined, t: ReturnType<typeof useLocale>['t']): string {
  if (!state || state.agentId !== agent.id || state.status === 'loading') return t('admin.overview.loadingConnection')
  if (state.status === 'error') return t('admin.overview.connectionUnavailable')
  if (state.config.base_url) return t('admin.overview.directSummary', { model: state.config.model ?? t('admin.overview.unknownModel') })
  if (agent.engine === 'pi-cli') return t('admin.overview.piSummary', { provider: state.config.provider ?? t('admin.overview.noProvider'), model: state.config.model ?? t('admin.overview.noModel') })
  return t('admin.overview.codexSummary', { model: state.config.model ?? defaultModel ?? t('admin.overview.defaultModel') })
}
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

const DEPRECATED_BADGE_CSS =
  'border-[color:color-mix(in_srgb,var(--color-warning)_40%,transparent)] bg-[color:color-mix(in_srgb,var(--color-warning)_8%,transparent)] text-[10px] text-[var(--color-warning)]'

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
      provider?: string | null
      provider_set?: boolean
      model_set?: boolean
      reasoning_effort?: string | null
      reasoning_effort_set?: boolean
      turn_timeout_sec?: number | null
      turn_timeout_sec_set?: boolean
      permission_level?: string | null
      permission_level_set?: boolean
      description?: string | null
      description_set?: boolean
    },
  ) => Promise<Agent>
  /** Issue #217 — populate the Model / Reasoning dropdowns. Optional
   *  so existing tests that don't care about config editing keep
   *  passing; when absent, the rows render in read-only fallback. */
  fetchEngineCatalog?: (engine: string) => Promise<EngineCatalog | null>
  connectionState?: ConnectionState | null
}

export default function OverviewPanel({ agent, updateAgent, fetchEngineCatalog, connectionState }: Props) {
  const { t } = useLocale()
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
  // Issue #493 — per-agent turn timeout (seconds). Blur-commit like
  // ``nameDraft``; empty clears back to the global default. A dedicated
  // error state keeps the range-validation message in its own row.
  const [turnTimeoutDraft, setTurnTimeoutDraft] = useState(
    agent?.turn_timeout_sec != null ? String(agent.turn_timeout_sec) : '',
  )
  const [turnTimeoutError, setTurnTimeoutError] = useState<string | null>(null)

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

  if (!agent) {
    return (
      <div
        className="flex h-full items-center justify-center text-caption text-[var(--color-foreground-subtle)]"
        data-testid="overview-panel-empty"
      >
        {t('admin.overview.noAgent')}
      </div>
    )
  }

  const machineOffline = agent.machine_online === false
  const agentOnline = deriveAgentOnline(agent.actual_state, { machineOffline })
  const rawDisplayState = agentStatusLabel(agent.actual_state, { machineOffline })
  const stateLabels: Record<string, string> = {
    unreachable: t('admin.agentSettings.state.unreachable'),
    unknown: t('admin.agentSettings.state.unknown'),
    running: t('admin.agentSettings.state.running'),
    starting: t('admin.agentSettings.state.starting'),
    stopping: t('admin.agentSettings.state.stopping'),
    stopped: t('admin.agentSettings.state.stopped'),
    idle: t('admin.agentSettings.state.idle'),
    pending: t('admin.agentSettings.state.pending'),
    crashed: t('admin.agentSettings.state.crashed'),
    failed: t('admin.agentSettings.state.failed'),
  }
  const displayState = stateLabels[rawDisplayState] ?? rawDisplayState

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

  // #493 — commit the turn timeout on blur. Empty input clears the
  // override (null + _set). The API enforces the numeric range (30 .. below
  // the orphan threshold) and returns 422, surfaced via ``turnTimeoutError``.
  const handleTurnTimeoutCommit = async () => {
    const trimmed = turnTimeoutDraft.trim()
    const nextVal = trimmed === '' ? null : Number(trimmed)
    if (nextVal !== null && (!Number.isInteger(nextVal) || nextVal <= 0)) {
      setTurnTimeoutError(t('admin.overview.timeoutInvalid'))
      return
    }
    if ((agent.turn_timeout_sec ?? null) === nextVal) {
      setTurnTimeoutError(null)
      return
    }
    setConfigSaving(true)
    setTurnTimeoutError(null)
    try {
      await updateAgent(agent.id, {
        turn_timeout_sec: nextVal,
        turn_timeout_sec_set: true,
      })
    } catch (e) {
      setTurnTimeoutError(e instanceof Error ? e.message : String(e))
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
          aria-label={t('admin.overview.changeAvatar')}
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
            aria-label={t('admin.overview.agentName')}
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
            placeholder={t('admin.overview.descriptionPlaceholder')}
            aria-label={t('admin.overview.agentDescription')}
            data-testid="overview-description-input"
            className="flex w-full resize-none rounded-[var(--radius-xs)] border border-[var(--color-border-strong)] bg-[var(--color-background)] px-3 py-2 text-sm text-[var(--color-foreground)] placeholder:text-[var(--color-foreground-subtle)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-brand-focus)] disabled:opacity-60"
          />
          <div className="flex items-center justify-between text-[11px] text-[var(--color-foreground-subtle)]">
            <span>{t('admin.overview.descriptionVisibility')}</span>
            <span data-testid="overview-description-counter">{descriptionDraft.length}/200</span>
          </div>
          {/* #644 — every agent now receives the room roster, so this
              line is what teammates' models read when deciding whom to
              ask. An empty one leaves them a bare name to guess from,
              which is a misrouting risk rather than an error — hence
              caution orange (DESIGN.md §2: warning is heads-up, danger
              is destructive) and no icon, keeping it quieter than the
              error row below. */}
          {descriptionDraft.trim() === '' ? (
            <div
              className="text-[11px] text-[var(--color-warning)]"
              data-testid="overview-description-empty-hint"
            >
              {t('admin.overview.descriptionEmpty')}
            </div>
          ) : null}
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
            title={t('admin.overview.copyId')}
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
              {t('admin.overview.copied')}
            </span>
          ) : copyState === 'fallback' ? (
            <span
              className="text-xs text-[var(--color-foreground-muted)]"
              data-testid="overview-copy-feedback"
            >
              {t('admin.overview.clipboardUnavailable')}
            </span>
          ) : null}
        </dd>

        <dt className="text-[var(--color-foreground-muted)]">{t('admin.overview.engine')}</dt>
        <dd className="flex items-center gap-2 text-[var(--color-foreground)]">
          <span>{agent.engine}</span>
          {catalogState.kind === 'ready' && catalogState.catalog.deprecated ? (
            <Badge
              variant="outline"
              className={DEPRECATED_BADGE_CSS}
              title={catalogState.catalog.deprecation_note ?? undefined}
            >
              {t('admin.overview.deprecated')}
            </Badge>
          ) : null}
        </dd>

        {(agent.engine === 'pi-cli' || agent.engine === 'codex-cli') && (
          <>
            <dt className="text-[var(--color-foreground-muted)]">{t('admin.overview.connection')}</dt>
            <dd data-testid="overview-connection-summary">{connectionSummary(agent, connectionState, catalogState.kind === 'ready' ? catalogState.catalog.default_model : undefined, t)}</dd>
          </>
        )}

        {/* #493 — Per-agent turn timeout (seconds). Blank = global default.
            Engine-agnostic, so it renders for every agent (outside the
            catalog-gated Model/Reasoning block). Blur-commits; the range is
            validated server-side and a 422 surfaces in ``turnTimeoutError``. */}
        <dt className="text-[var(--color-foreground-muted)]">{t('admin.overview.turnTimeout')}</dt>
        <dd>
          <input
            type="number"
            inputMode="numeric"
            min={30}
            step={10}
            value={turnTimeoutDraft}
            onChange={e => setTurnTimeoutDraft(e.target.value)}
            onBlur={() => void handleTurnTimeoutCommit()}
            disabled={configSaving}
            placeholder={t('admin.overview.default')}
            aria-label={t('admin.overview.timeoutLabel')}
            data-testid="overview-turn-timeout-input"
            className={SELECT_CSS}
          />
          {turnTimeoutError ? (
            <div
              className="mt-1 flex items-center gap-1 text-xs text-[var(--color-warning)]"
              data-testid="overview-turn-timeout-error"
            >
              <AlertCircle className="h-3 w-3" aria-hidden="true" />
              {turnTimeoutError}
            </div>
          ) : (
            <p className="mt-1 text-[11px] text-[var(--color-foreground-muted)]">
              {t('admin.overview.timeoutHelp')}
            </p>
          )}
        </dd>

        {/* #309 — Permission tier. Sits next to Model / Reasoning so
            it groups with the other adapter-spawn parameters. The
            REST endpoint is admin-only so non-admin users get a 403
            on PATCH; UI gating is "best effort" — we still render
            the select so non-admins see the current value, but the
            change is rejected at the API. ``trusted`` carries an
            inline ⚠ to flag host access. */}
        <dt className="text-[var(--color-foreground-muted)]">{t('admin.overview.permission')}</dt>
        <dd>
          <select
            value={agent.permission_level ?? ''}
            onChange={e => void handlePermissionLevelChange(e.target.value)}
            disabled={configSaving}
            aria-label={t('admin.overview.permissionTier')}
            data-testid="overview-permission-select"
            className={SELECT_CSS}
          >
            <option value="">{t('admin.overview.permissionDefault')}</option>
            <option value="restricted">{t('admin.overview.permissionRestricted')}</option>
            <option value="standard">{t('admin.overview.permissionStandard')}</option>
            <option value="trusted">{t('admin.overview.permissionTrusted')}</option>
          </select>
          {configError && <p role="alert" data-testid="overview-config-error">{configError}</p>}
          {agent.permission_level === 'trusted' ? (
            <p
              className="mt-1 text-[11px] text-[var(--color-foreground-muted)]"
              data-testid="overview-permission-trusted-warning"
            >
              {t('admin.overview.trustedWarning')}
            </p>
          ) : null}
        </dd>

        <dt className="text-[var(--color-foreground-muted)]">{t('admin.overview.state')}</dt>
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

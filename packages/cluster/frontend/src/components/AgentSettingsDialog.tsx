/**
 * AgentSettingsDialog — unified agent settings (#158, restructured #165).
 *
 * Renders overview, connection, manifest, rooms, responsibilities,
 * tasks, and activity in a single scrollable dialog body. Stacking
 * the sections lets the admin scan the agent and scroll to a section.
 *
 * Panel lifecycle: every panel is always mounted when the dialog is
 * open. Unsaved Manifest edits therefore survive scrolling to other
 * sections (the earlier conditional-render design discarded them).
 *
 * Save semantics are section-scoped: Overview auto-saves on blur
 * (name) and on pick (avatar), Rooms mutates on click, Activity is
 * read-only, Manifest keeps its own bulk Save button. The dialog
 * itself has no footer bar.
 *
 * Style: follows DESIGN.md's teal tokens and light/dark surfaces.
 */
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import PresenceDot from '@/components/PresenceDot'
import { agentStatusLabel, deriveAgentOnline } from '@/lib/agent-liveness'
import type { Agent, AgentFile, AttachedSkill, SkillPreview, EngineCatalog } from '@/hooks/useAgents'
import OverviewPanel from '@/components/agent-settings/OverviewPanel'
import ModelConnectionPanel, { type ConnectionState } from '@/components/agent-settings/ModelConnectionPanel'
import ManifestPanel from '@/components/agent-settings/ManifestPanel'
import RoomsPanel from '@/components/agent-settings/RoomsPanel'
import ActivityPanel from '@/components/agent-settings/ActivityPanel'
import TasksPanel from '@/components/agent-settings/TasksPanel'
import GoalsPanel from '@/components/agent-settings/GoalsPanel'
import WorkspacePanel from '@/components/agent-settings/WorkspacePanel'
import { ChevronRight, EyeOff, Trash2, Check } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Select } from '@/components/ui/select'
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { useLocale } from '@/i18n/LocaleProvider'

interface Props {
  agent: Agent | null
  open: boolean
  onOpenChange: (open: boolean) => void
  fetchAgentFiles: (id: string) => Promise<AgentFile[]>
  updateAgent: (
    id: string,
    patch: {
      name?: string
      agents_md?: string | null
      agents_md_set?: boolean
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
      description?: string | null
      description_set?: boolean
    },
  ) => Promise<Agent>
  upsertAgentFile: (id: string, path: string, content: string) => Promise<AgentFile>
  deleteAgentFile: (id: string, path: string) => Promise<void>
  fetchAttachedSkills?: (id: string) => Promise<AttachedSkill[]>
  fetchSkillPreview?: (skillId: string) => Promise<SkillPreview | null>
  /** Issue #217 — lets the Overview panel populate Model / Reasoning
   *  dropdowns. Returns ``null`` for engines the catalog doesn't
   *  know about (e.g. ``echo`` dev-only); OverviewPanel hides the
   *  dropdowns in that case. */
  fetchEngineCatalog?: (engine: string) => Promise<EngineCatalog | null>
  /** Fired when Rooms panel mutates so the caller can refresh its
   *  own derived state (e.g. comma-joined room names in a machine
   *  detail view). */
  onRoomsChange?: () => void
  /** #435 — option parity with ``AgentSettingsMenu``. When supplied, a
   *  footer surfaces the same per-agent admin actions the row menu has,
   *  so the action set no longer differs by entry point. Each renders
   *  only when its handler is provided ("show-when-permitted"). */
  onDelete?: () => void
  /** Current value of the context-window opt-out flag. Paired with
   *  ``onToggleContextWindowOptOut``: the footer renders a check-mark
   *  toggle when both are provided. */
  contextWindowOptOut?: boolean
  onToggleContextWindowOptOut?: () => void | Promise<void>
}

// Shared section labels use the same readable form hierarchy. Same classes are
// reused for the collapsible `<summary>` so both section types look
// identical.
const SECTION_HEADING_CLASS =
  'text-sm font-semibold text-[var(--color-foreground)]'

// Elevated cards sit on the alternate surface in both themes.
const SECTION_CARD_CLASS =
  'scroll-mt-4 bg-[var(--color-surface-elevated)] rounded-[var(--radius-md)] border border-[var(--color-border)] p-4'

function Section({
  id,
  title,
  children,
}: {
  id: string
  title: string
  children: ReactNode
}) {
  return (
    <section
      id={`agent-settings-${id}`}
      tabIndex={-1}
      aria-labelledby={`agent-settings-heading-${id}`}
      data-testid={`agent-settings-section-${id}`}
      className={`${SECTION_CARD_CLASS} space-y-3`}
    >
      <h3 id={`agent-settings-heading-${id}`} className={SECTION_HEADING_CLASS}>
        {title}
      </h3>
      {children}
    </section>
  )
}

/**
 * Same card chrome as `<Section>` but the body is collapsed behind a
 * native `<details>` so low-frequency sections (e.g. Activity) don't
 * steal scroll real estate from Manifest/Rooms by default. A rotating
 * chevron signals the collapsible affordance.
 */
function CollapsibleSection({
  id,
  title,
  children,
  defaultOpen = false,
}: {
  id: string
  title: string
  children: ReactNode
  defaultOpen?: boolean
}) {
  return (
    <details
      id={`agent-settings-${id}`}
      data-testid={`agent-settings-section-${id}`}
      className={`${SECTION_CARD_CLASS} group`}
      open={defaultOpen}
    >
      <summary
        className={`${SECTION_HEADING_CLASS} flex min-h-[var(--control-sm-height)] items-center justify-between gap-3 cursor-pointer list-none select-none`}
        aria-labelledby={`agent-settings-heading-${id}`}
      >
        <span id={`agent-settings-heading-${id}`}>{title}</span>
        <ChevronRight className="h-4 w-4 shrink-0 transition-transform group-open:rotate-90" aria-hidden="true" />
      </summary>
      <div className="mt-3">{children}</div>
    </details>
  )
}

export default function AgentSettingsDialog({
  agent,
  open,
  onOpenChange,
  fetchAgentFiles,
  updateAgent,
  upsertAgentFile,
  deleteAgentFile,
  fetchAttachedSkills,
  fetchSkillPreview,
  fetchEngineCatalog,
  onRoomsChange,
  onDelete,
  contextWindowOptOut,
  onToggleContextWindowOptOut,
}: Props) {
  const { t } = useLocale()
  const bodyRef = useRef<HTMLDivElement>(null)
  const [selectedSection, setSelectedSection] = useState('overview')
  const [connectionState, setConnectionState] = useState<ConnectionState | null>(null)
  const onConnectionChange = useCallback((next: ConnectionState) => setConnectionState(next), [])
  useEffect(() => {
    if (!open) setConnectionState(null)
    if (open) setSelectedSection('overview')
  }, [open])
  const machineOffline = agent?.machine_online === false
  const agentOnline = deriveAgentOnline(agent?.actual_state, { machineOffline })
  const rawDisplayState = agentStatusLabel(agent?.actual_state, { machineOffline })
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
  const sections = [
    { id: 'overview', label: t('admin.agentSettings.overview') },
    ...(agent && (agent.engine === 'codex-cli' || agent.engine === 'pi-cli')
      ? [{ id: 'model-connection', label: t('agentSetup.connectionNav') }] : []),
    { id: 'workspace', label: t('agentSetup.workspace') },
    { id: 'manifest', label: t('agentSetup.instructionsNav') },
    { id: 'rooms', label: t('admin.agentSettings.rooms') },
    { id: 'goals', label: t('admin.agentSettings.responsibilities') },
    { id: 'tasks', label: t('admin.agentSettings.tasks') },
    { id: 'activity', label: t('admin.agentSettings.activity') },
  ]
  function jumpToSection(id: string) {
    const section = bodyRef.current?.querySelector<HTMLElement>(`#agent-settings-${id}`)
    if (!section) return
    setSelectedSection(id)
    if (section instanceof HTMLDetailsElement) section.open = true
    section.scrollIntoView?.({ block: 'start' })
    const focusTarget = section instanceof HTMLDetailsElement ? section.querySelector('summary') : section
    focusTarget?.focus({ preventScroll: true })
  }

  // Footer option parity with AgentSettingsMenu (#435): the toggle row
  // appears only when both the value and its handler are supplied.
  const showContextToggle =
    typeof contextWindowOptOut === 'boolean' &&
    typeof onToggleContextWindowOptOut === 'function'

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-4xl max-h-[90dvh] overflow-hidden flex flex-col p-0 gap-0">
        <DialogHeader className="border-b border-[var(--color-border)] px-4 pb-3 pt-5 pr-14 sm:px-6 sm:pr-14">
          <DialogTitle className="flex min-w-0 flex-col items-start gap-1.5 pr-8 text-left sm:flex-row sm:items-center sm:gap-2">
            <span className="shrink-0">{t('admin.agentSettings.title')}</span>
            {agent ? (
              <span className="inline-flex min-w-0 max-w-full items-center gap-1.5 text-sm font-normal text-[var(--color-foreground-muted)]">
                <span className="hidden text-[var(--color-foreground-subtle)] sm:inline">—</span>
                <PresenceDot
                  variant="agent"
                  online={agentOnline}
                  agentState={displayState}
                />
                <span className="min-w-0 max-w-[20rem] truncate">
                  {agent.name}
                </span>
                <span className="shrink-0 text-[var(--color-foreground-subtle)]">
                  ({agent.engine})
                </span>
              </span>
            ) : null}
          </DialogTitle>
          <DialogDescription className="text-left text-xs leading-relaxed">
            {t('agentSetup.saveHint')}
          </DialogDescription>
        </DialogHeader>
        <nav aria-label={t('agentSetup.settingsNavigation')} className="shrink-0 border-b border-[var(--color-border)] px-4 py-2 sm:px-6">
          <Select className="md:hidden" aria-label={t('agentSetup.settingsNavigation')} value={selectedSection} onChange={event => jumpToSection(event.target.value)}>
            {sections.map(section => <option key={section.id} value={section.id}>{section.label}</option>)}
          </Select>
          <div className="hidden flex-wrap gap-1 md:grid md:grid-cols-4 lg:flex">
            {sections.map(section => <Button key={section.id} variant={selectedSection === section.id ? 'secondary' : 'ghost'} size="sm" aria-controls={`agent-settings-${section.id}`} onClick={() => jumpToSection(section.id)}>{section.label}</Button>)}
          </div>
        </nav>

        {/* A single scrollable body separates elevated section cards
            from the alternate surface in both light and dark themes. */}
        <div ref={bodyRef} className="flex-1 min-h-0 overflow-y-auto bg-[var(--color-surface-alt)]">
          <div className="px-3 py-4 space-y-3 sm:px-6 sm:py-5">
            <Section id="overview" title={t('admin.agentSettings.overview')}>
              <OverviewPanel
                agent={agent}
                updateAgent={updateAgent}
                fetchEngineCatalog={fetchEngineCatalog}
                connectionState={connectionState}
              />
            </Section>

            {agent && (agent.engine === 'codex-cli' || agent.engine === 'pi-cli') && (
              <Section id="model-connection" title={t('admin.agentSettings.modelConnection')}>
                <ModelConnectionPanel
                  key={agent.id}
                  agent={agent}
                  updateAgent={updateAgent}
                  fetchEngineCatalog={fetchEngineCatalog}
                  onConnectionChange={onConnectionChange}
                />
              </Section>
            )}

            <Section id="workspace" title={t('agentSetup.workspace')}>
              <WorkspacePanel agentId={agent?.id ?? null} onNavigateAway={() => onOpenChange(false)} />
            </Section>

            <Section id="manifest" title={t('agentSetup.filesInstructions')}>
              <ManifestPanel
                agent={agent}
                fetchAgentFiles={fetchAgentFiles}
                updateAgent={updateAgent}
                upsertAgentFile={upsertAgentFile}
                deleteAgentFile={deleteAgentFile}
                fetchAttachedSkills={fetchAttachedSkills}
                fetchSkillPreview={fetchSkillPreview}
                onNavigateAway={() => onOpenChange(false)}
              />
            </Section>

            <Section id="rooms" title={t('admin.agentSettings.rooms')}>
              <RoomsPanel agentId={agent?.id ?? null} onChange={onRoomsChange} />
            </Section>

            {/* Goals (#302) — recurring responsibilities the agent
                owns. Above Tasks because "what is this agent committed
                to over time" is a higher-level question than "what's
                open right now". */}
            <Section id="goals" title={t('admin.agentSettings.responsibilities')}>
              <GoalsPanel
                agentId={agent?.id ?? null}
                agentName={agent?.name ?? ''}
              />
            </Section>

            {/* Tasks (#266) — cross-room aggregation of work currently
                assigned to this agent. Sits next to Rooms because both
                answer "what is this agent doing right now". */}
            <Section id="tasks" title={t('admin.agentSettings.tasks')}>
              <TasksPanel agentId={agent?.id ?? null} />
            </Section>

            {/* Activity is a lifecycle log — least-often consulted of
                the four sections. Collapsed by default keeps Manifest
                and Rooms closer to the top of the scroll. */}
            <CollapsibleSection id="activity" title={t('admin.agentSettings.activity')}>
              <ActivityPanel agentId={agent?.id ?? null} />
            </CollapsibleSection>
          </div>
        </div>

        {/* Footer (#435) — per-agent admin actions, at parity with the
            row menu so the action set no longer depends on entry point.
            Renders only when at least one handler is supplied. */}
        {(showContextToggle || onDelete) && (
          <div className="flex shrink-0 flex-col gap-1 border-t border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-4 py-2 sm:flex-row sm:items-center sm:justify-between sm:gap-3 sm:px-6 sm:py-3">
            {showContextToggle ? (
              <button
                type="button"
                role="switch"
                aria-checked={contextWindowOptOut}
                onClick={() => void onToggleContextWindowOptOut!()}
                data-testid="agent-settings-context-window-opt-out"
                className="inline-flex min-h-[var(--control-height)] w-full items-center gap-2 rounded-[var(--radius-sm)] px-2 py-1.5 text-left text-sm text-[var(--color-foreground)] hover:bg-[var(--color-surface-hover)] cursor-pointer sm:w-auto"
              >
                <EyeOff className="h-4 w-4" />
                <span>{t('admin.agentSettings.contextOptOut')}</span>
                {contextWindowOptOut ? (
                  <Check className="h-4 w-4 text-[var(--color-brand-text)]" aria-hidden="true" />
                ) : null}
              </button>
            ) : (
              <span aria-hidden="true" className="hidden sm:block" />
            )}
            {onDelete ? (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => onDelete()}
                data-testid="agent-settings-delete"
                className="w-full justify-start text-[var(--color-destructive)] hover:bg-[var(--color-destructive)]/10 sm:w-auto"
              >
                <Trash2 className="h-4 w-4" />
                {t('admin.agentSettings.deleteAgent')}
              </Button>
            ) : null}
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

/**
 * AgentSettingsDialog — unified agent settings (#158, #165, #715).
 *
 * Four tabs group the destinations by purpose: Settings (overview,
 * model connection, instructions), Work (rooms, responsibilities,
 * tasks), Workspace (files) and Activity. The tabs really switch views;
 * inside Settings a separate list of links scrolls to its sections.
 *
 * Panel lifecycle: every tab panel stays mounted while the dialog is
 * open (inactive ones are hidden), so unsaved Manifest or workspace
 * edits survive switching tabs.
 *
 * Save semantics are section-scoped: Overview auto-saves on blur
 * (name) and on pick (avatar), Rooms mutates on click, Activity is
 * read-only, Manifest keeps its own bulk Save button.
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
import RecentTurnSummary from '@/components/agent-settings/RecentTurnSummary'
import TasksPanel from '@/components/agent-settings/TasksPanel'
import GoalsPanel from '@/components/agent-settings/GoalsPanel'
import WorkspacePanel from '@/components/agent-settings/WorkspacePanel'
import { EyeOff, Trash2, Check } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Select } from '@/components/ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
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

type SettingsTab = 'settings' | 'work' | 'workspace' | 'activity'

const SECTION_TAB: Record<string, SettingsTab> = {
  overview: 'settings',
  'model-connection': 'settings',
  manifest: 'settings',
  rooms: 'work',
  goals: 'work',
  tasks: 'work',
  workspace: 'workspace',
  activity: 'activity',
}

// Inactive panels stay mounted (forceMount) and are only hidden.
const TAB_PANEL_CLASS =
  'mt-0 space-y-3 px-3 py-4 sm:px-6 sm:py-5 data-[state=inactive]:hidden'

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
  const [tab, setTab] = useState<SettingsTab>('settings')
  const [focusRequestId, setFocusRequestId] = useState<string | null>(null)
  const [connectionState, setConnectionState] = useState<ConnectionState | null>(null)
  const onConnectionChange = useCallback((next: ConnectionState) => setConnectionState(next), [])
  useEffect(() => {
    if (!open) {
      setConnectionState(null)
      setFocusRequestId(null)
    }
    if (open) setTab('settings')
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
  const hasConnection = !!agent && (agent.engine === 'codex-cli' || agent.engine === 'pi-cli')
  const tabs: { id: SettingsTab; label: string }[] = [
    { id: 'settings', label: t('agentSetup.tabSettings') },
    { id: 'work', label: t('agentSetup.tabWork') },
    { id: 'workspace', label: t('agentSetup.tabWorkspace') },
    { id: 'activity', label: t('agentSetup.tabActivity') },
  ]
  const settingsSections = [
    { id: 'overview', label: t('admin.agentSettings.overview') },
    ...(hasConnection ? [{ id: 'model-connection', label: t('agentSetup.connectionNav') }] : []),
    { id: 'manifest', label: t('agentSetup.instructionsNav') },
  ]
  function changeTab(next: SettingsTab) {
    setTab(next)
    bodyRef.current?.scrollTo?.({ top: 0 })
  }
  // Opens the section's tab, then scrolls to it once the panel is visible.
  function jumpToSection(id: string) {
    const target = SECTION_TAB[id]
    if (!target) return
    setTab(target)
    requestAnimationFrame(() => {
      const section = bodyRef.current?.querySelector<HTMLElement>(`#agent-settings-${id}`)
      section?.scrollIntoView?.({ block: 'start' })
      section?.focus({ preventScroll: true })
    })
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
        <Tabs value={tab} onValueChange={value => changeTab(value as SettingsTab)} className="flex min-h-0 flex-1 flex-col">
          <TabsList
            aria-label={t('agentSetup.settingsNavigation')}
            className="grid h-auto w-full shrink-0 grid-cols-4 justify-stretch gap-1 rounded-none border-b border-[var(--color-border)] bg-transparent px-2 py-0 sm:flex sm:justify-start sm:px-6"
          >
            {tabs.map(item => (
              <TabsTrigger
                key={item.id}
                value={item.id}
                data-testid={`agent-settings-tab-${item.id}`}
                className="-mb-px h-11 rounded-none border-b-2 border-transparent px-2 data-[state=active]:border-[var(--color-brand)] data-[state=active]:bg-transparent data-[state=active]:text-[var(--color-brand-text)] data-[state=active]:shadow-none sm:px-3"
              >
                {item.label}
              </TabsTrigger>
            ))}
          </TabsList>

          {/* One scroll body for all tabs; switching tabs resets it to the top. */}
          <div ref={bodyRef} className="flex-1 min-h-0 overflow-y-auto bg-[var(--color-surface-alt)]">
            <TabsContent value="settings" forceMount className={TAB_PANEL_CLASS}>
              {/* In-page links: they scroll within Settings, unlike the tabs above. */}
              <nav aria-label={t('agentSetup.settingsSections')} className="flex items-center gap-2">
                <span className="shrink-0 text-xs text-[var(--color-foreground-muted)]">{t('agentSetup.jumpTo')}</span>
                <Select className="sm:hidden" aria-label={t('agentSetup.settingsSections')} value="" onChange={event => jumpToSection(event.target.value)}>
                  <option value="" disabled>{t('agentSetup.chooseSection')}</option>
                  {settingsSections.map(section => <option key={section.id} value={section.id}>{section.label}</option>)}
                </Select>
                <div className="hidden flex-wrap gap-x-3 gap-y-1 sm:flex">
                  {settingsSections.map(section => (
                    <button
                      key={section.id}
                      type="button"
                      aria-controls={`agent-settings-${section.id}`}
                      onClick={() => jumpToSection(section.id)}
                      className="min-h-[var(--control-sm-height)] text-sm text-[var(--color-brand-text)] underline-offset-4 hover:underline cursor-pointer"
                    >
                      {section.label}
                    </button>
                  ))}
                </div>
              </nav>
              <Section id="overview" title={t('admin.agentSettings.overview')}>
                <OverviewPanel
                  agent={agent}
                  updateAgent={updateAgent}
                  fetchEngineCatalog={fetchEngineCatalog}
                  connectionState={connectionState}
                  recentTurn={
                    <RecentTurnSummary
                      agentId={agent?.id ?? null}
                      active={open}
                      onShowTurn={requestId => {
                        setFocusRequestId(requestId)
                        changeTab('activity')
                      }}
                    />
                  }
                />
              </Section>

              {agent && hasConnection && (
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
            </TabsContent>

            <TabsContent value="work" forceMount className={TAB_PANEL_CLASS}>
              <Section id="rooms" title={t('admin.agentSettings.rooms')}>
                <RoomsPanel agentId={agent?.id ?? null} onChange={onRoomsChange} />
              </Section>

              {/* Goals (#302) — recurring responsibilities, above Tasks
                  because commitments over time frame what is open now. */}
              <Section id="goals" title={t('admin.agentSettings.responsibilities')}>
                <GoalsPanel
                  key={agent?.id}
                  onNavigateAway={() => onOpenChange(false)}
                  agentId={agent?.id ?? null}
                  agentName={agent?.name ?? ''}
                />
              </Section>

              {/* Tasks (#266) — cross-room work currently assigned. */}
              <Section id="tasks" title={t('admin.agentSettings.tasks')}>
                <TasksPanel agentId={agent?.id ?? null} onNavigateAway={() => onOpenChange(false)} />
              </Section>
            </TabsContent>

            <TabsContent value="workspace" forceMount className={TAB_PANEL_CLASS}>
              <Section id="workspace" title={t('agentSetup.workspace')}>
                <WorkspacePanel agentId={agent?.id ?? null} onNavigateAway={() => onOpenChange(false)} />
              </Section>
            </TabsContent>

            {/* Activity polls only while its tab is visible. */}
            <TabsContent value="activity" forceMount className={TAB_PANEL_CLASS}>
              <Section id="activity" title={t('admin.agentSettings.activity')}>
                <ActivityPanel agentId={agent?.id ?? null} active={open && tab === 'activity'} focusRequestId={focusRequestId} />
              </Section>
            </TabsContent>
          </div>
        </Tabs>

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
